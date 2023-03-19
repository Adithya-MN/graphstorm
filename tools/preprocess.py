""" Preprocess the input data """
from multiprocessing import Process
import multiprocessing
import glob
import os
import json
import pyarrow.parquet as pq
import pyarrow as pa
import numpy as np
from functools import partial
import argparse

from transformers import BertTokenizer
import torch as th

##################### The I/O functions ####################

def read_data_parquet(data_file):
    """ Read data from the parquet file.

    Parameters
    ----------
    data_file : str
        The parquet file that contains the data

    Returns
    -------
    dict : map from data name to data.
    """
    table = pq.read_table(data_file)
    pd = table.to_pandas()
    return {key: np.array(pd[key]) for key in pd}

read_data_funcs = {
    "parquet": read_data_parquet,
}

def get_read_data_func(fmt):
    return read_data_funcs[fmt['name']]

############## The functions for parsing configurations #############

def parse_tokenize(op):
    """ Parse the tokenization configuration

    The parser returns a function that tokenizes text with HuggingFace tokenizer.
    The tokenization function returns a dict of three Pytorch tensors.

    Parameters
    ----------
    op : dict
        The configuration for the operation.

    Returns
    -------
    callable : a function to process the data.
    """
    tokenizer = BertTokenizer.from_pretrained(op['bert_model'])
    max_seq_length = int(op['max_seq_length'])
    def tokenize(file_idx, strs):
        tokens = []
        att_masks = []
        type_ids = []
        for s in strs:
            t = tokenizer(s, max_length=max_seq_length,
                          truncation=True, padding='max_length', return_tensors='pt')
            tokens.append(t['input_ids'])
            att_masks.append(t['attention_mask'])
            type_ids.append(t['token_type_ids'])
        return {'token_ids': th.cat(tokens, dim=0),
                'attention_mask': th.cat(att_masks, dim=0),
                'token_type_ids': th.cat(type_ids, dim=0)}
    return tokenize

def parse_feat_ops(confs):
    """ Parse the configurations for processing the features

    The feature transformation:
    {
        "feature_col":  ["<column name>", ...],
        "feature_name": "<feature name>",
        "data_type":    "<feature data type>",
        "transform":    {"name": "<operator name>", ...}
    }

    Parameters
    ----------
    confs : list
        A list of feature transformations.

    Returns
    -------
    list of tuple : The operations
    """
    ops = []
    for feat in confs:
        dtype = None
        if 'transform' not in feat:
            transform = None
        elif transform['name'] == 'tokenize_hf':
            trasnform = parse_tokenize(transform)
        else:
            raise ValueError('Unknown operation: {}'.format(transform['name']))
        ops.append((feat['feature_col'], feat['feature_name'], dtype, transform))
    return ops

#################### The main function for processing #################

def process_data(data, ops):
    """ Process the data with the specified operations.

    This function runs the input operations on the corresponding data
    and returns the processed results.

    Parameters
    ----------
    data : dict
        The data stored as a dict.
    ops : list of tuples
        The operations. Each tuple contains two elements. The first element
        is the data name and the second element is a Python function
        to process the data.

    Returns
    -------
    dict : the key is the data name, the value is the processed data.
    """
    new_data = {}
    for feat_col, feat_name, dtype, op in ops:
        # If the transformation is defined on the feature.
        if op is not None:
            res = op(data[feat_col])
        # If the required data type is defined on the feature.
        elif dtype is not None:
            res = data[feat_col].astype(dtype)
        # If no transformation is defined for the feature.
        else:
            res = data[feat_col]
        new_data[feat_name] = res
    return new_data

################### The functions for multiprocessing ###############

def wait_process(q, max_proc):
    """ Wait for a process

    Parameters
    ----------
    q : list of process
        The list of processes
    max_proc : int
        The maximal number of processes to process the data together.
    """
    if len(q) < max_proc:
        return
    q[0].join()
    q.pop(0)
    
def wait_all(q):
    """ Wait for all processes

    Parameters
    ----------
    q : list of processes
        The list of processes
    """
    for p in q:
        p.join()
        
def get_in_files(in_files):
    """ Get the input files.

    The input file string may contains a wildcard. This function
    gets all files that meet the requirement.

    Parameters
    ----------
    in_files : a str or a list of str
        The input files.

    Returns
    -------
    a list of str : the full name of input files.
    """
    if '*' in in_files:
        in_files = glob.glob(in_files)
    elif not isinstance(in_files, list):
        in_files = [in_files]
    in_files.sort()
    return in_files

def parse_node_data(i, in_file, feat_ops, node_id_col, read_file, return_dict):
    data = read_file(in_file)
    data = process_data(i, data, feat_ops)
    return_vals[i] = (data[node_id_col], data)

def parse_edge_data(i, in_file, feat_ops, src_id_col, dst_id_col, edge_type,
                    node_id_map, read_file, return_dict):
    data = read_file(in_file)
    data = process_data(i, data, feat_ops)
    src_ids = data[src_id_col]
    dst_ids = data[dst_id_col]
    if node_id_map is None:
        assert np.issubdtype(src_ids.dtype, np.integer), \
                "The source node Ids have to be integer."
        assert np.issubdtype(dst_ids.dtype, np.integer), \
                "The destination node Ids have to be integer."
    else:
        src_type, _, dst_type = edge_type
        src_ids = [node_id_map[src_type][sid] for sid in src_ids]
        dst_ids = [node_id_map[dst_type][did] for did in dst_ids]
        src_ids = np.concatenate(src_ids)
        dst_ids = np.concatenate(dst_ids)
    return_vals[i] = (src_ids, dst_ids, data)

def create_id_map(ids):
    return {id1: i for i, id1 in enumerate(ids)}

def process_node_data(process_confs, remap_id):
    """ Process node data

    We need to process all node data before we can process edge data.
    Processing node data will generate the ID mapping.

    The node data of a node type is defined as follows:
    {
        "node_id_col":  "<column name>",
        "node_type":    "<node type>",
        "format":       {"name": "csv", "separator": ","},
        "files":        ["<paths to files>", ...],
        "features":     [
            {
                "feature_col":  ["<column name>", ...],
                "feature_name": "<feature name>",
                "data_type":    "<feature data type>",
                "transform":    {"name": "<operator name>", ...}
            },
        ],
        "labels":       [
            {
                "label_col":    "<column name>",
                "task_type":    "<task type: e.g., classification>",
                "split_type":   [0.8, 0.2, 0.0],
                "custom_train": "<the file with node IDs in the train set>",
                "custom_valid": "<the file with node IDs in the validation set>",
                "custom_test":  "<the file with node IDs in the test set>",
            },
        ],
    }

    Parameters
    ----------
    process_confs: list of dicts
        The configurations to process node data.
    remap_id: bool
        Whether or not to remap node IDs

    Returns
    -------
    dict: node ID map
    dict: node features.
    """
    node_data = {}
    for process_conf in process_confs:
        node_id_col = process_conf['node_id_col']
        node_type = process_conf['node_type']
        feat_ops = parse_feat_ops(process_conf['features'])
        feat_names = [feat_op['feature_name'] for feat_op in process_conf['features']]
        q = []
        manager = multiprocessing.Manager()
        return_dict = manager.dict()
        read_file = parse_file_format(process_conf['format'])
        in_files = get_in_files(process_conf['files'])
        for i, in_file in enumerate(in_files):
            p = Process(target=parse_node_data, args=(i, in_file, feat_ops, node_id_col,
                                                      read_file, return_dict))
            p.start()
            q.append(p)
            wait_process(q, num_processes)
        wait_all(q)

        type_node_data = {feat_name: [None] * len(return_dict) \
                for feat_name in feat_names}
        type_node_id_map = [None] * len(return_dict)
        for i, (node_ids, data) in return_dict.items():
            for feat_name in data:
                type_node_data[feat_name][i] = data[feat_name]
            type_node_id_map[i] = node_ids

        for feat_name in type_node_data:
            type_node_data[feat_name] = np.concatenate(type_node_data[feat_name])
        assert type_node_id_map[0] is not None
        type_node_id_map = np.concatenate(type_node_id_map)
        if np.issubdtype(type_node_id_map.dtype, np.integer) \
                # If all node Ids are in sequence start from 0.
                and np.all(type_node_id_map == np.arange(len(type_node_id_map))) \
                # If the user doesn't force to remap node IDs.
                and not remap_id:
            type_node_id_map = None
        else:
            type_node_id_map = create_id_map(type_node_id_map)

        node_data[node_type] = type_node_data
        node_id_map[node_type] = type_node_id_map

    return (node_id_map, node_data)

def process_edge_data(process_confs, node_id_map):
    """ Process edge data

    The edge data of an edge type is defined as follows:
    {
        "source_id_col":    "<column name>",
        "dest_id_col":      "<column name>",
        "relation":         "<src type, relation type, dest type>",
        "format":           {"name": "csv", "separator": ","},
        "files":            ["<paths to files>", ...],
        "features":         [
            {
                "feature_col":  ["<column name>", ...],
                "feature_name": "<feature name>",
                "data_type":    "<feature data type>",
                "transform":    {"name": "<operator name>", ...}
            },
        ],
        "labels":           [
            {
                "label_col":    "<column name>",
                "task_type":    "<task type: e.g., classification>",
                "split_type":   [0.8, 0.2, 0.0],
                "custom_train": "<the file with node IDs in the train set>",
                "custom_valid": "<the file with node IDs in the validation set>",
                "custom_test":  "<the file with node IDs in the test set>",
            },
        ],
    }

    Parameters
    ----------
    process_confs: list of dicts
        The configurations to process edge data.
    node_id_map: dict
        The node ID map.

    Returns
    -------
    dict: edge features.
    """
    edges = {}
    edge_data = {}

    for process_conf in process_confs:
        src_id_col = process_conf['source_id_col']
        dst_id_col = process_conf['dest_id_col']
        edge_type = process_conf['relation']
        feat_ops = parse_feat_ops(process_conf['features'])
        feat_names = [feat_op['feature_name'] for feat_op in process_conf['features']]
        q = []
        manager = multiprocessing.Manager()
        return_dict = manager.dict()
        read_file = parse_file_format(process_conf['format'])
        in_files = get_in_files(process_conf['files'])
        for i, in_file in enumerate(in_files):
            p = Process(target=parse_edge_data, args=(i, in_file, feat_ops,
                                                      src_id_col, dst_id_col, edge_type,
                                                      node_id_map, read_file, return_dict))
            p.start()
            q.append(p)
            wait_process(q, num_processes)
        wait_all(q)

        type_edges = [None] * len(return_dict)
        type_edge_data = {feat_name: [None] * len(return_dict) \
                for feat_name in feat_names}
        for i, (part_edges, part_data) in return_dict.items():
            type_edges[i] = part_edges
            for feat_name in data:
                type_edge_data[feat_name][i] = part_data[feat_name]

        for feat_name in type_edge_data:
            type_edge_data[feat_name] = np.concatenate(type_edge_data[feat_name])

        edges[edge_type] = type_edges
        edge_data[edge_type] = type_edge_data

    return edges, edge_data

if __name__ == '__main__':
    argparser = argparse.ArgumentParser("Preprocess graphs")
    argparser.add_argument("--conf_file", type=str, required=True,
            help="The configuration file.")
    argparser.add_argument("--num_processes", type=int, default=1,
            help="The number of processes to process the data simulteneously.")
    argparser.add_argument("--output_dir", type=str, required=True,
            help="The path of the output data folder.")
    argparser.add_argument("--remap_node_id", type=bool, default=False,
            help="Whether or not to remap node IDs.")
    args = argparser.parse_args()
    num_processes = args.num_processes
    process_confs = json.load(open(args.conf_file, 'r'))

    node_id_map, node_data = process_node_data(process_confs, args.remap_node_id)
    edges, edge_data = process_edge_data(process_confs, node_id_map)
    num_nodes = {ntype: len(node_data[ntype]) for ntype in node_data}
    g = dgl.heterograph(edges, num_nodes_dict=num_nodes)
    for ntype in node_data:
        for name, data in node_data[ntype].items():
            g.nodes[ntype].data[name] = data
    for etype in edge_data:
        for name, data in edge_data[etype].items():
            g.edges[etype].data[name] = data