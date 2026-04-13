.PHONY: all pre-commit mypy unit-test unit-test-junit unit-test-cov-html unit-test-cov-xml diff-cover unit-test-diff-cover

CMD:=uv run -m
PYMODULE:=pyrit
TESTS:=tests
UNIT_TESTS:=tests/unit
INTEGRATION_TESTS:=tests/integration
PARTNER_INTEGRATION_TESTS:=tests/partner_integration
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
	$(CMD) pytest -n 4 --dist=loadfile $(UNIT_TESTS)

unit-test-junit:
	$(CMD) pytest -n 4 --dist=loadfile $(UNIT_TESTS) --junitxml=junit/test-results.xml

unit-test-cov-html:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) --cov-fail-under=78 $(UNIT_TESTS) --cov-report html

unit-test-cov-xml:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) --cov-fail-under=78 $(UNIT_TESTS) --cov-report xml --cov-report term

diff-cover:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) --cov-fail-under=78 $(UNIT_TESTS) --cov-report xml
	uv run python -m diff_cover.diff_cover_tool coverage.xml --compare-branch=origin/main --diff-range-notation=.. --fail-under=90

unit-test-diff-cover:
	uv run python -m diff_cover.diff_cover_tool coverage.xml --compare-branch=origin/main --diff-range-notation=.. --fail-under=90

integration-test:
	$(CMD) pytest $(INTEGRATION_TESTS) --cov=$(PYMODULE) $(INTEGRATION_TESTS) --cov-report xml --junitxml=junit/test-results.xml --doctest-modules

end-to-end-test:
	$(CMD) pytest $(END_TO_END_TESTS) -v --junitxml=junit/test-results.xml

partner-integration-test:
	$(CMD) pytest $(PARTNER_INTEGRATION_TESTS) -v --junitxml=junit/partner-test-results.xml

#clean:
#	git clean -Xdf # Delete all files in .gitignore
