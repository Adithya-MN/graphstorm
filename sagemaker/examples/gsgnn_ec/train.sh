#!/bin/bash
python3 graphstorm/sagemaker/launch_train.py  --version-tag sagemaker_v3 --training-ecr-repository graphstorm_alpha --account-id ACCOUNT_ID --region us-east-1 --role IAM_ROLE --graph-name movie-lens-100k --graph-data-s3 S3_PATH_TO_GRAPH_DATA --task-type "edge_classification" --model-artifact-s3 S3_PATH_TO_STORE_SAVED_MODEL --train-yaml-s3 S3_PATH_TO_TRAIN_CONFIG --train-yaml-name ml_ec.yaml --n-layers 2 --n-hidden 128 --backend gloo --batch-size 128 --train-nodes 0 --bert-infer-bs 64 --fanout 10,5