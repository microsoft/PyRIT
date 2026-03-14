.PHONY: all pre-commit mypy test test-cov-html test-cov-xml

CMD:=uv run -m
PYMODULE:=pyrit
TESTS:=tests
UNIT_TESTS:=tests/unit
INTEGRATION_TESTS:=tests/integration
END_TO_END_TESTS:=tests/end_to_end

all: pre-commit

pre-commit:
	$(CMD) isort --multi-line 3 --recursive $(PYMODULE) $(TESTS)
	pre-commit run --all-files

mypy:
	$(CMD) mypy $(PYMODULE) $(UNIT_TESTS)

# Build the full documentation site:
# 1. Generate API reference JSON from Python source (griffe)
# 2. Convert API JSON to MyST markdown pages
# 3. Build the Jupyter Book site
# 4. Generate RSS feed
docs-build:
	uv run python build_scripts/pydoc2json.py pyrit --submodules -o doc/_api/pyrit_all.json
	uv run python build_scripts/gen_api_md.py
	cd doc && uv run jupyter-book build --all --html
	uv run ./build_scripts/generate_rss.py

# Regenerate only the API reference pages (without building the full site)
docs-api:
	uv run python build_scripts/pydoc2json.py pyrit --submodules -o doc/_api/pyrit_all.json
	uv run python build_scripts/gen_api_md.py

# Because of import time, "auto" seemed to actually go slower than just using 4 processes
unit-test:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) $(UNIT_TESTS)

unit-test-cov-html:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) $(UNIT_TESTS) --cov-report html

unit-test-cov-xml:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) $(UNIT_TESTS) --cov-report xml --junitxml=junit/test-results.xml --doctest-modules

integration-test:
	$(CMD) pytest $(INTEGRATION_TESTS) --cov=$(PYMODULE) $(INTEGRATION_TESTS) --cov-report xml --junitxml=junit/test-results.xml --doctest-modules

end-to-end-test:
	$(CMD) pytest $(END_TO_END_TESTS) -v --junitxml=junit/test-results.xml

#clean:
#	git clean -Xdf # Delete all files in .gitignore
