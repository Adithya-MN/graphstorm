""" Train GSgnn custom model.
"""

import argparse
import dgl
import torch as th
import graphstorm as gs
from graphstorm import model as gsmodel
from graphstorm.trainer import GSgnnNodePredictionTrainer
from graphstorm.dataloading import GSgnnNodeTrainData, GSgnnNodeDataLoader

class MyGNNModel(gsmodel.GSgnnNodeModelBase):
    def __init__(self, g, feat_size, num_hidden, num_classes):
        super(MyGNNModel, self).__init__()
        self._node_input = gsmodel.GSNodeInputLayer(g, feat_size, num_hidden)
        self._gnn = gsmodel.RelationalGCNEncoder(g, num_hidden, num_hidden, num_hidden_layers=1)
        self._decoder = gsmodel.EntityClassifier(num_hidden, num_classes, multilabel=False)
        self._loss_fn = gsmodel.ClassifyLossFunc(multilabel=False)

    def forward(self, blocks, node_feats, _, labels):
        input_nodes = {ntype: blocks[0].srcnodes[ntype].data[dgl.NID].cpu() \
                for ntype in blocks[0].srctypes}
        embs = self._node_input(node_feats, input_nodes)
        embs = self._gnn(blocks, embs)
        target_ntype = list(labels.keys())[0]
        emb = embs[target_ntype]
        labels = labels[target_ntype]
        logits = self._decoder(emb)
        return self._loss_fn(logits, labels)

    def predict(self, blocks, node_feats, _):
        input_nodes = {ntype: blocks[0].srcnodes[ntype].data[dgl.NID].cpu() \
                for ntype in blocks[0].srctypes}
        embs = self._node_input(node_feats, input_nodes)
        embs = {name: emb.to(device) for name, emb in embs.items()}
        embs = self._gnn(blocks, embs)
        assert len(embs) == 1
        emb = list(embs.values())[0]
        return self._decoder.predict(emb)

    def restore_model(self, restore_model_path):
        pass

    def save_model(self, model_path):
        pass

    def create_optimizer(self):
        # Here we don't set up an optimizer for sparse embeddings.
        return th.optim.Adam(self.parameters(), lr=0.001)

def main(args):
    gs.initialize(ip_config=args.ip_config, backend="gloo")
    train_data = GSgnnNodeTrainData(args.graph_name,
                                    args.part_config,
                                    train_ntypes=args.predict_ntype,
                                    node_feat_field=args.node_feat,
                                    label_field=args.label)
    for ntype in train_data.g.ntypes:
        print(ntype, train_data.g.nodes[ntype].data.keys())
    feat_size = gs.get_feat_size(train_data.g, args.node_feat)
    model = MyGNNModel(train_data.g, feat_size, 16, args.num_classes)
    trainer = GSgnnNodePredictionTrainer(model, gs.get_rank(), topk_model_to_save=1)
    trainer.setup_cuda(dev_id=args.local_rank)
    device = 'cuda:%d' % trainer.dev_id
    dataloader = GSgnnNodeDataLoader(train_data, train_data.train_idxs, fanout=[10, 10],
                                     batch_size=1000, device=device, train_task=True)
    trainer.fit(train_loader=dataloader, n_epochs=2)

if __name__ == '__main__':
    argparser = argparse.ArgumentParser("Training GNN model")
    argparser.add_argument("--ip-config", type=str, required=True,
                           help="The IP config file for the cluster.")
    argparser.add_argument("--graph-name", type=str, required=True,
                           help="The graph name.")
    argparser.add_argument("--part-config", type=str, required=True,
                           help="The partition config file.")
    argparser.add_argument("--predict-ntype", type=str, required=True,
                           help="The node type for prediction.")
    argparser.add_argument("--node-feat", type=str, required=True,
                           help="The name of the node feature.")
    argparser.add_argument("--label", type=str, required=True,
                           help="The name of the label.")
    argparser.add_argument("--num-classes", type=int, required=True,
                           help="The number of classes.")
    argparser.add_argument("--local_rank", type=int,
                           help="The rank of the trainer.")
    args = argparser.parse_args()
    main(args)