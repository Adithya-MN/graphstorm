#!/bin/bash

service ssh restart

DGL_HOME=/root/dgl
GS_HOME=$(pwd)
NUM_TRAINERS=4
NUM_INFO_TRAINERS=2
export PYTHONPATH=$GS_HOME/python/
cd $GS_HOME/training_scripts/gsgnn_er
echo "127.0.0.1" > ip_list.txt
cd $GS_HOME/inference_scripts/ep_infer
echo "127.0.0.1" > ip_list.txt

error_and_exit () {
	# check exec status of launch.py
	status=$1
	echo $status

	if test $status -ne 0
	then
		exit -1
	fi
}

echo "**************dataset: ML edge regression, RGCN layer: 1, node feat: fixed HF BERT, BERT nodes: movie, inference: mini-batch"
python3 $DGL_HOME/tools/launch.py --workspace $GS_HOME/training_scripts/gsgnn_er/ --num_trainers $NUM_TRAINERS --num_servers 1 --num_samplers 0 --part_config /data/movielen_100k_er_1p_4t/movie-lens-100k.json --ip_config ip_list.txt --ssh_port 2222 "python3 gsgnn_er_huggingface.py --cf ml_er.yaml --num-gpus $NUM_TRAINERS --part-config /data/movielen_100k_er_1p_4t/movie-lens-100k.json --save-embeds-path /data/gsgnn_er/emb/ --save-model-path /data/gsgnn_er/ --topk-model-to-save 3 --save-model-per-iter 1000 --n-epochs 3" | tee train_log.txt

error_and_exit $?

# check prints
cnt=$(grep "save_embeds_path: /data/gsgnn_er/emb/" train_log.txt | wc -l)
if test $cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have save_embeds_pathy"
    exit -1
fi

cnt=$(grep "save_model_path: /data/gsgnn_er/" train_log.txt | wc -l)
if test $cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have save_model_path"
    exit -1
fi

cnt=$(grep "Train rmse" train_log.txt | wc -l)
if test $cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have Train rmse"
    exit -1
fi

bst_cnt=$(grep "Best Test rmse" train_log.txt | wc -l)
if test $bst_cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have Best Test rmse"
    exit -1
fi

cnt=$(grep "| Test rmse" train_log.txt | wc -l)
if test $cnt -lt $((1+$bst_cnt))
then
    echo "We use SageMaker task tracker, we should have Test rmse"
    exit -1
fi

bst_cnt=$(grep "Best Validation rmse" train_log.txt | wc -l)
if test $bst_cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have Best Validation rmse"
    exit -1
fi

cnt=$(grep "Validation rmse" train_log.txt | wc -l)
if test $cnt -lt $((1+$bst_cnt))
then
    echo "We use SageMaker task tracker, we should have Validation rmse"
    exit -1
fi

cnt=$(grep "Best Iteration" train_log.txt | wc -l)
if test $cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have Best Iteration"
    exit -1
fi

cnt=$(ls -l /data/gsgnn_er/ | grep epoch | wc -l)
if test $cnt != 3
then
    echo "The number of save models $cnt is not equal to the specified topk 3"
    exit -1
fi

echo "**************dataset: ML edge regression, do inference on saved model"
python3 $DGL_HOME/tools/launch.py --workspace $GS_HOME/inference_scripts/ep_infer --num_trainers $NUM_INFO_TRAINERS --num_servers 1 --num_samplers 0 --part_config /data/movielen_100k_er_1p_4t/movie-lens-100k.json --ip_config ip_list.txt --ssh_port 2222 "python3 ep_infer_huggingface.py --cf ml_er_infer.yaml --num-gpus $NUM_INFO_TRAINERS --part-config /data/movielen_100k_er_1p_4t/movie-lens-100k.json --mini-batch-infer false --save-embeds-path /data/gsgnn_er/infer-emb/ --restore-model-path /data/gsgnn_er/epoch-2/" | tee log.txt

error_and_exit $?

cnt=$(grep "| Test rmse" log.txt | wc -l)
if test $cnt -ne 1
then
    echo "We do test, should have test rmse"
    exit -1
fi

bst_cnt=$(grep "Best Test rmse" log.txt | wc -l)
if test $bst_cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have Best Test rmse"
    exit -1
fi

bst_cnt=$(grep "Best Validation rmse" log.txt | wc -l)
if test $bst_cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have Best Validation rmse"
    exit -1
fi

cnt=$(grep "Validation rmse" log.txt | wc -l)
if test $cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have Validation rmse"
    exit -1
fi

cnt=$(grep "Best Iteration" log.txt | wc -l)
if test $cnt -lt 1
then
    echo "We use SageMaker task tracker, we should have Best Iteration"
    exit -1
fi

cd $GS_HOME/tests/end2end-tests/
python3 check_infer.py --train_embout /data/gsgnn_er/emb/epoch-2/ --infer_embout /data/gsgnn_er/infer-emb/ --edge_prediction

error_and_exit $?
