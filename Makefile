deploy-models:
	AWS_ACCESS_KEY_ID=$${AWS_ACCESS_KEY_ID} \
	AWS_SECRET_ACCESS_KEY=$${AWS_SECRET_ACCESS_KEY} \
	python infrastructure/src/models/deploy_sagemaker_endpoint.py --model-folder=embedding --max-concurrency 3;
	

deploy:
	cd infrastructure && cdk bootstrap && cdk deploy && cd .. && deploy-models


initialize-db:
	python setup_db.py --secret-name DBSecretD58955BC-cvl1N4Uq6XVw --ssl-path /Users/tetracycline/repos/rag-tutorial/us-west-2-bundle.pem --seed-data-path /Users/tetracycline/data/hubspot_data_cleaned