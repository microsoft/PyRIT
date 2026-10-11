.PHONY: all pre-commit ty docs-api docs-build docs-build-pdf docs-build-all unit-test unit-test-junit unit-test-cov-html unit-test-cov-xml diff-cover unit-test-diff-cover

CMD:=uv run -m
PYMODULE:=pyrit
TESTS:=tests
UNIT_TESTS:=tests/unit
INTEGRATION_TESTS:=tests/integration
PARTNER_INTEGRATION_TESTS:=tests/partner_integration
END_TO_END_TESTS:=tests/end_to_end
JUNIT_XML?=junit/test-results.xml
DIFF_COVER_BASE?=origin/main

all: pre-commit

pre-commit:
	$(CMD) isort --multi-line 3 --recursive $(PYMODULE) $(TESTS)
	pre-commit run --all-files

ty:
	$(CMD) ty check $(PYMODULE) $(UNIT_TESTS)

# Build strict HTML and the RSS feed after generating the API reference.
# --all would also select the configured PDF export and require LaTeX.
docs-build: docs-api
	cd doc && uv run jupyter-book build --html --strict
	uv run python -m build_scripts.generate_rss

# PDF is a separate, checked export requiring latexmk and xelatex.
docs-build-pdf: docs-api
	uv run python -m build_scripts.build_docs_pdf

# Build HTML first, then check the PDF export without repeating API generation.
docs-build-all: docs-build
	uv run python -m build_scripts.build_docs_pdf

# Regenerate only the API reference pages (without building the full site)
docs-api:
	uv run python -m build_scripts.pydoc2json pyrit --submodules -o doc/_api/pyrit_all.json
	uv run python -m build_scripts.gen_api_md

# Because of import time, "auto" seemed to actually go slower than just using 4 processes
unit-test:
	$(CMD) pytest -n 4 --dist=loadfile $(UNIT_TESTS)

unit-test-junit:
	$(CMD) pytest -n 4 --dist=loadfile $(UNIT_TESTS) --junitxml=$(JUNIT_XML) --durations=25

unit-test-cov-html:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) --cov-fail-under=78 $(UNIT_TESTS) --cov-report html

unit-test-cov-xml:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) --cov-fail-under=78 $(UNIT_TESTS) --cov-report xml --cov-report term --junitxml=$(JUNIT_XML) --durations=25

diff-cover:
	$(CMD) pytest -n 4 --dist=loadfile --cov=$(PYMODULE) --cov-fail-under=78 $(UNIT_TESTS) --cov-report xml
	uv run python -m diff_cover.diff_cover_tool coverage.xml --compare-branch="$(DIFF_COVER_BASE)" --diff-range-notation=.. --fail-under=90

unit-test-diff-cover:
	uv run python -m diff_cover.diff_cover_tool coverage.xml --compare-branch="$(DIFF_COVER_BASE)" --diff-range-notation=.. --fail-under=90

integration-test:
	$(CMD) pytest $(INTEGRATION_TESTS) --cov=$(PYMODULE) --cov-report xml --junitxml=$(JUNIT_XML) --doctest-modules

end-to-end-test:
	$(CMD) pytest $(END_TO_END_TESTS) -v --junitxml=$(JUNIT_XML)

partner-integration-test: JUNIT_XML=junit/test-results-partner.xml
partner-integration-test:
	$(CMD) pytest $(PARTNER_INTEGRATION_TESTS) -v --junitxml=$(JUNIT_XML)

#clean:
#	git clean -Xdf # Delete all files in .gitignore
