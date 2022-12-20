import torch as th
from torch import nn
import numpy as np
from numpy.testing import assert_almost_equal

import dgl
from graphstorm.model import GSNodeInputLayer
from graphstorm.model.embed import compute_node_input_embeddings
from graphstorm.model.utils import get_feat_size

from data_utils import generate_dummy_dist_graph

# In this case, we only use the node features to generate node embeddings.
def test_input_layer1():
    # get the test dummy distributed graph
    g = generate_dummy_dist_graph()

    feat_size = get_feat_size(g, 'feat')
    layer = GSNodeInputLayer(g, feat_size, 2)
    ntypes = list(layer.input_projs.keys())
    assert set(ntypes) == set(g.ntypes)
    node_feat = {}
    input_nodes = {}
    for ntype in ntypes:
        # We make the projection matrix a diagonal matrix so that
        # the input and output matrices are identical.
        nn.init.eye_(layer.input_projs[ntype])
        input_nodes[ntype] = np.arange(10)
        node_feat[ntype] = g.nodes[ntype].data['feat'][input_nodes[ntype]]
    embed = layer(node_feat, input_nodes)
    assert len(embed) == len(input_nodes)
    assert len(embed) == len(node_feat)
    for ntype in embed:
        assert_almost_equal(embed[ntype].detach().numpy(),
                            node_feat[ntype].detach().numpy())
    dgl.distributed.kvstore.close_kvstore()

# In this case, we use both node features and sparse embeddings.
def test_input_layer2():
    # get the test dummy distributed graph
    g = generate_dummy_dist_graph()

    feat_size = get_feat_size(g, 'feat')
    layer = GSNodeInputLayer(g, feat_size, 2, use_node_embeddings=True)
    assert set(layer.input_projs.keys()) == set(g.ntypes)
    assert set(layer.sparse_embeds.keys()) == set(g.ntypes)
    assert set(layer.proj_matrix.keys()) == set(g.ntypes)
    node_feat = {}
    node_embs = {}
    input_nodes = {}
    for ntype in g.ntypes:
        # We make the projection matrix a diagonal matrix so that
        # the input and output matrices are identical.
        nn.init.eye_(layer.input_projs[ntype])
        assert layer.proj_matrix[ntype].shape == (4, 2)
        # We make the projection matrix that can simply add the node features
        # and the node sparse embeddings after projection.
        with th.no_grad():
            layer.proj_matrix[ntype][:2,:] = layer.input_projs[ntype]
            layer.proj_matrix[ntype][2:,:] = layer.input_projs[ntype]
        input_nodes[ntype] = np.arange(10)
        node_feat[ntype] = g.nodes[ntype].data['feat'][input_nodes[ntype]]
        node_embs[ntype] = layer.sparse_embeds[ntype].weight[input_nodes[ntype]]
    embed = layer(node_feat, input_nodes)
    assert len(embed) == len(input_nodes)
    assert len(embed) == len(node_feat)
    for ntype in embed:
        true_val = node_feat[ntype].detach().numpy() + node_embs[ntype].detach().numpy()
        assert_almost_equal(embed[ntype].detach().numpy(), true_val)
    dgl.distributed.kvstore.close_kvstore()

# In this case, we use node feature on one node type and
# use sparse embedding on the other node type.
def test_input_layer3():
    # get the test dummy distributed graph
    g = generate_dummy_dist_graph()

    feat_size = get_feat_size(g, {'n0' : 'feat'})
    layer = GSNodeInputLayer(g, feat_size, 2)
    assert len(layer.input_projs) == 1
    assert list(layer.input_projs.keys())[0] == 'n0'
    assert len(layer.sparse_embeds) == 1
    node_feat = {}
    node_embs = {}
    input_nodes = {}
    for ntype in g.ntypes:
        input_nodes[ntype] = np.arange(10)
    nn.init.eye_(layer.input_projs['n0'])
    node_feat['n0'] = g.nodes['n0'].data['feat'][input_nodes['n0']]
    node_embs['n1'] = layer.sparse_embeds['n1'].weight[input_nodes['n1']]
    embed = layer(node_feat, input_nodes)
    assert len(embed) == len(input_nodes)
    assert_almost_equal(embed['n0'].detach().numpy(), node_feat['n0'].detach().numpy())
    assert_almost_equal(embed['n1'].detach().numpy(), node_embs['n1'].detach().numpy())

    # Test the case with errors.
    try:
        embed = layer(node_feat, {'n2': 'feat'})
    except:
        embed = None
    assert embed is None

    # test the case that one node type has no input nodes.
    input_nodes['n0'] = np.arange(10)
    input_nodes['n1'] = np.zeros((0,))
    nn.init.eye_(layer.input_projs['n0'])
    node_feat['n0'] = g.nodes['n0'].data['feat'][input_nodes['n0']]
    node_embs['n1'] = layer.sparse_embeds['n1'].weight[input_nodes['n1']]
    embed = layer(node_feat, input_nodes)
    assert len(embed) == len(input_nodes)
    assert_almost_equal(embed['n0'].detach().numpy(), node_feat['n0'].detach().numpy())
    assert_almost_equal(embed['n1'].detach().numpy(), node_embs['n1'].detach().numpy())
    dgl.distributed.kvstore.close_kvstore()

def test_compute_embed():
    # get the test dummy distributed graph
    g = generate_dummy_dist_graph()
    # initialize the torch distributed environment
    th.distributed.init_process_group(backend='nccl',
                                      init_method='tcp://127.0.0.1:23456',
                                      rank=0,
                                      world_size=1)
    print('g has {} nodes of n0 and {} nodes of n1'.format(
        g.number_of_nodes('n0'), g.number_of_nodes('n1')))

    feat_size = get_feat_size(g, {'n0' : 'feat'})
    layer = GSNodeInputLayer(g, feat_size, 2)
    nn.init.eye_(layer.input_projs['n0'])

    embeds = compute_node_input_embeddings(g, 10, layer,
                                           feat_field={'n0' : 'feat'})
    assert len(embeds) == len(g.ntypes)
    assert_almost_equal(embeds['n0'][0:len(embeds['n1'])].numpy(),
            g.nodes['n0'].data['feat'][0:g.number_of_nodes('n0')].numpy())
    assert_almost_equal(embeds['n1'][0:len(embeds['n1'])].numpy(),
            layer.sparse_embeds['n1'].weight[0:g.number_of_nodes('n1')].numpy())
    # Run it again to tigger the branch that access 'input_emb' directly.
    embeds = compute_node_input_embeddings(g, 10, layer,
                                           feat_field={'n0' : 'feat'})
    th.distributed.destroy_process_group()
    dgl.distributed.kvstore.close_kvstore()

if __name__ == '__main__':
    test_input_layer1()
    test_input_layer2()
    test_input_layer3()
    test_compute_embed()