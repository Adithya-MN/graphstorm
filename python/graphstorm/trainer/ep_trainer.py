""" GraphStorm trainer for edge prediction """
import time

import dgl
import torch as th
from torch.nn.parallel import DistributedDataParallel

from ..model.edge_gnn import edge_mini_batch_gnn_predict, edge_mini_batch_predict
from ..model.gnn import do_full_graph_inference
from .gsgnn_trainer import GSgnnTrainer

from ..utils import sys_tracker

class GSgnnEdgePredictionTrainer(GSgnnTrainer):
    """ Edge prediction trainer.

    Parameters
    ----------
    model : GSgnnNodeModel
        The GNN model for node prediction.
    rank : int
        The rank.
    topk_model_to_save : int
        The top K model to save.
    """

    def fit(self, train_loader, n_epochs,
            val_loader=None,
            test_loader=None,
            mini_batch_infer=True,
            save_model_path=None,
            save_model_per_iters=None):
        """ The fit function for edge prediction.

        Parameters
        ----------
        train_loader : GSgnnEdgeDataLoader
            The mini-batch sampler for training.
        n_epochs : int
            The max number of epochs to train the model.
        val_loader : GSgnnEdgeDataLoader
            The mini-batch sampler for computing validation scores. The validation scores
            are used for selecting models.
        test_loader : GSgnnEdgeDataLoader
            The mini-batch sampler for computing test scores.
        mini_batch_infer : bool
            Whether or not to use mini-batch inference.
        save_model_path : str
            The path where the model is saved.
        save_model_per_iters : int
            The number of iteration to train the model before saving the model.
        """
        # Check the correctness of configurations.
        if self.evaluator is not None:
            assert val_loader is not None, \
                    "The evaluator is provided but validation set is not provided."

        model = DistributedDataParallel(self._model, device_ids=[self.dev_id],
                                        output_device=self.dev_id)
        device = model.device

        # training loop
        dur = []
        best_epoch = 0
        num_input_nodes = 0
        forward_time = 0
        back_time = 0
        total_steps = 0
        early_stop = False # used when early stop is True
        sys_tracker.check('start training')
        data = train_loader.data
        for epoch in range(n_epochs):
            model.train()
            t0 = time.time()
            for i, (input_nodes, batch_graph, blocks) in enumerate(train_loader):
                total_steps += 1
                batch_tic = time.time()

                if not isinstance(input_nodes, dict):
                    assert len(batch_graph.ntypes) == 1
                    input_nodes = {batch_graph.ntypes[0]: input_nodes}
                input_feats = data.get_node_feats(input_nodes, device)
                # retrieving seed edge id from the graph to find labels
                # TODO(zhengda) expand code for multiple edge types
                assert len(batch_graph.etypes) == 1
                predict_etype = batch_graph.canonical_etypes[0]
                # TODO(zhengda) the data loader should return labels directly.
                seeds = batch_graph.edges[predict_etype[1]].data[dgl.EID]
                lbl = data.get_labels({predict_etype: seeds}, device)
                blocks = [block.to(device) for block in blocks]
                batch_graph = batch_graph.to(device)
                for _, nodes in input_nodes.items():
                    num_input_nodes += nodes.shape[0]

                t2 = time.time()
                loss = model(blocks, batch_graph, input_feats, input_nodes, lbl)

                t3 = time.time()
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()
                forward_time += (t3 - t2)
                back_time += (time.time() - t3)

                self.log_metric("Train loss", loss.item(), total_steps)

                if i % 20 == 0 and self.rank == 0:
                    # Print task specific info.
                    print(
                        "Part {} | Epoch {:05d} | Batch {:03d} | Train Loss: {:.4f} | Time: {:.4f}".
                        format(self.rank, epoch, i, loss.item(), time.time() - batch_tic))
                    num_input_nodes = forward_time = back_time = 0

                val_score = None
                if self.evaluator is not None and \
                    self.evaluator.do_eval(total_steps, epoch_end=False):
                    val_score = self.eval(model.module, val_loader, test_loader, mini_batch_infer,
                                          total_steps)

                    if self.evaluator.do_early_stop(val_score):
                        early_stop = True

                # Every n iterations, check to save the top k models. If has validation score,
                # will save # the best top k. But if no validation, will either save
                # the last k model or all models depends on the setting of top k
                if save_model_per_iters > 0 and i % save_model_per_iters == 0 and i != 0:
                    self.save_topk_models(model, epoch, i, val_score, save_model_path)

                # early_stop, exit current interation.
                if early_stop is True:
                    break

            # ------- end of an epoch -------

            th.distributed.barrier()
            epoch_time = time.time() - t0
            if self.rank == 0:
                print("Epoch {} take {}".format(epoch, epoch_time))
            dur.append(epoch_time)

            val_score = None
            if self.evaluator is not None and self.evaluator.do_eval(total_steps, epoch_end=True):
                val_score = self.eval(model.module, val_loader, test_loader, mini_batch_infer,
                                      total_steps)

                if self.evaluator.do_early_stop(val_score):
                    early_stop = True

            # After each epoch, check to save the top k models. If has validation score, will save
            # the best top k. But if no validation, will either save the last k model or all models
            # depends on the setting of top k. To show this is after epoch save, set the iteration
            # to be None, so that we can have a determistic model folder name for testing and debug.
            self.save_topk_models(model, epoch, None, val_score, save_model_path)

            th.distributed.barrier()

            # early_stop, exit training
            if early_stop is True:
                break

        print("Peak Mem alloc: {:.4f} MB".format(th.cuda.max_memory_allocated(device) / 1024 /1024))
        if self.rank == 0 and self.evaluator is not None:
            output = dict(best_test_score=self.evaluator.best_test_score,
                          best_val_score=self.evaluator.best_val_score,
                          peak_mem_alloc_MB=th.cuda.max_memory_allocated(device) / 1024 / 1024,
                          best_epoch=best_epoch)
            self.log_params(output)

            if self.save_perf_results_path is not None:
                self.save_model_results_to_file(self.evaluator.best_test_score)

    def eval(self, model, val_loader, test_loader, mini_batch_infer, total_steps):
        """ do the model evaluation using validiation and test sets

        Parameters
        ----------
        model : Pytorch model
            The GNN model.
        val_loader: GSNodeDataLoader
            The dataloader for validation data
        test_loader : GSNodeDataLoader
            The dataloader for test data.
        mini_batch_infer : bool
            Whether or not to use mini-batch inference.
        total_steps: int
            Total number of iterations.

        Returns
        -------
        float: validation score
        """
        test_start = time.time()
        sys_tracker.check('start prediction')
        if mini_batch_infer:
            val_pred, val_label = edge_mini_batch_gnn_predict(model, val_loader,
                                                              return_label=True)
            test_pred, test_label = edge_mini_batch_gnn_predict(model, test_loader,
                                                                return_label=True)
        else:
            emb = do_full_graph_inference(model, val_loader.data, task_tracker=self.task_tracker)
            val_pred, val_label = edge_mini_batch_predict(model, emb, val_loader,
                                                          return_label=True)
            test_pred, test_label = edge_mini_batch_predict(model, emb, test_loader,
                                                            return_label=True)
        sys_tracker.check('predict')
        val_score, test_score = self.evaluator.evaluate(val_pred, test_pred,
                                                        val_label, test_label, total_steps)
        sys_tracker.check('evaluate')

        if self.rank == 0:
            self.log_print_metrics(val_score=val_score,
                                test_score=test_score,
                                dur_eval=time.time() - test_start,
                                total_steps=total_steps)
        return val_score
