.PHONY: demo test
demo:  ## One-command demo launch (snapshot live layer + open the app, offline-safe)
	./run.sh

test:  ## Run the test suite
	.venv/bin/pytest -q
