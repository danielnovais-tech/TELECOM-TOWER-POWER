# SAM Build Makefile - builds Lambda deployment packages
# Used by SAM CLI with BuildMethod: makefile

# Python source files needed by Lambda
LAMBDA_PY_FILES = \
	lambda_handler.py \
	sqs_lambda_worker.py \
	telecom_tower_power_api.py \
	telecom_tower_power.py \
	telecom_tower_power_db.py \
	models.py \
	pdf_generator.py \
	s3_storage.py \
	srtm_elevation.py \
	geocoder_br.py \
	tower_db.py \
	bedrock_service.py \
	coverage_predict.py \
	coverage_export.py \
	stripe_billing.py \
	stripe_webhook_service.py \
	job_store.py \
	batch_worker.py \
	key_store_db.py \
	repeater_jobs_store.py \
	hop_cache.py \
	graphql_schema.py \
	interference_engine.py \
	tracing.py

# Data files needed at runtime
LAMBDA_DATA_FILES = \
	geocode_cache_br.json \
	municipios_brasileiros.csv \
	coverage_model.npz \
	key_store.json

.PHONY: build-TelecomTowerPowerFunction build-BatchWorkerFunction build-BatchWorkerPriorityFunction

# Strip commands to reduce package size below Lambda 250MB limit
define strip_package
	# Remove tests, docs, __pycache__, .pyc, type stubs
	find $(ARTIFACTS_DIR) -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
	find $(ARTIFACTS_DIR) -type d -name "test" -exec rm -rf {} + 2>/dev/null || true
	find $(ARTIFACTS_DIR) -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find $(ARTIFACTS_DIR) -type d -name "*.dist-info" -exec rm -rf {} + 2>/dev/null || true
	find $(ARTIFACTS_DIR) -name "*.pyc" -delete 2>/dev/null || true
	find $(ARTIFACTS_DIR) -name "*.pyi" -delete 2>/dev/null || true
	# boto3/botocore already in Lambda runtime — remove them
	rm -rf $(ARTIFACTS_DIR)/boto3 $(ARTIFACTS_DIR)/botocore $(ARTIFACTS_DIR)/s3transfer 2>/dev/null || true
	rm -rf $(ARTIFACTS_DIR)/boto3-*.dist-info $(ARTIFACTS_DIR)/botocore-*.dist-info $(ARTIFACTS_DIR)/s3transfer-*.dist-info 2>/dev/null || true
	# Strip numpy test/typing extras
	rm -rf $(ARTIFACTS_DIR)/numpy/tests $(ARTIFACTS_DIR)/numpy/*/tests 2>/dev/null || true
	rm -rf $(ARTIFACTS_DIR)/numpy/typing/tests 2>/dev/null || true
	rm -rf $(ARTIFACTS_DIR)/numpy/f2py 2>/dev/null || true
	# Strip matplotlib sample data and test images
	rm -rf $(ARTIFACTS_DIR)/matplotlib/tests 2>/dev/null || true
	rm -rf $(ARTIFACTS_DIR)/matplotlib/mpl-data/sample_data 2>/dev/null || true
	rm -rf $(ARTIFACTS_DIR)/mpl_toolkits/tests 2>/dev/null || true
	# Strip fontTools (only needed by matplotlib for font subsetting)
	rm -rf $(ARTIFACTS_DIR)/fontTools/ttLib/tables/*_test* 2>/dev/null || true
endef

build-TelecomTowerPowerFunction:
	pip install -r requirements-lambda.txt -t $(ARTIFACTS_DIR)/ --quiet
	cp $(LAMBDA_PY_FILES) $(ARTIFACTS_DIR)/
	cp $(LAMBDA_DATA_FILES) $(ARTIFACTS_DIR)/ 2>/dev/null || true
	$(call strip_package)

build-BatchWorkerFunction:
	pip install -r requirements-lambda.txt -t $(ARTIFACTS_DIR)/ --quiet
	cp $(LAMBDA_PY_FILES) $(ARTIFACTS_DIR)/
	cp $(LAMBDA_DATA_FILES) $(ARTIFACTS_DIR)/ 2>/dev/null || true
	$(call strip_package)

build-BatchWorkerPriorityFunction:
	pip install -r requirements-lambda.txt -t $(ARTIFACTS_DIR)/ --quiet
	cp $(LAMBDA_PY_FILES) $(ARTIFACTS_DIR)/
	cp $(LAMBDA_DATA_FILES) $(ARTIFACTS_DIR)/ 2>/dev/null || true
	$(call strip_package)
