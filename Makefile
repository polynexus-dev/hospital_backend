# Polynexus HMS — build, test and bundle. Run from Backend/ on Linux, macOS
# or WSL (Windows). Needs Docker; the frontend repo is expected at ../Frontend.
#
#   make build                 images tagged $(VERSION)
#   make test                  backend + frontend tests
#   make bundle                dist/bundle-$(VERSION).tar.gz — everything an offline server needs
#
# Override as needed: make bundle VERSION=1.2.0 FRONTEND_DIR=/path/to/Frontend

VERSION      ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
REGISTRY     ?= polynexus
FRONTEND_DIR ?= ../Frontend
PLATFORM     ?= linux/amd64
PYTHON       ?= python3

BACKEND_IMAGE  := $(REGISTRY)/hms-backend:$(VERSION)
FRONTEND_IMAGE := $(REGISTRY)/hms-frontend:$(VERSION)
BASE_IMAGES    := postgres:16-alpine redis:7-alpine
BUNDLE_DIR     := dist/bundle-$(VERSION)
DEPLOY_FILES   := docker-compose.yml env.template install.sh install.ps1 upgrade.sh upgrade.ps1 configure-domain.sh configure-domain.ps1 backup.sh RUNBOOK.md README.md

.PHONY: build build-backend build-frontend test test-backend test-frontend bundle revocations clean

build: build-backend build-frontend

build-backend:
	docker build --platform $(PLATFORM) -t $(BACKEND_IMAGE) .

build-frontend:
	docker build --platform $(PLATFORM) -t $(FRONTEND_IMAGE) $(FRONTEND_DIR)

test: test-backend test-frontend

test-backend:
	$(PYTHON) -m pytest -q

test-frontend:
	cd $(FRONTEND_DIR) && npm ci && npx vitest run

bundle: revocations build
	rm -rf $(BUNDLE_DIR)
	mkdir -p $(BUNDLE_DIR)
	for img in $(BASE_IMAGES); do docker pull --platform $(PLATFORM) $$img; done
	docker save -o $(BUNDLE_DIR)/images.tar $(BACKEND_IMAGE) $(FRONTEND_IMAGE) $(BASE_IMAGES)
	cd deploy && cp $(DEPLOY_FILES) ../$(BUNDLE_DIR)/
	echo "$(VERSION)" > $(BUNDLE_DIR)/VERSION
	chmod +x $(BUNDLE_DIR)/install.sh $(BUNDLE_DIR)/upgrade.sh $(BUNDLE_DIR)/configure-domain.sh
	tar -C dist -czf dist/bundle-$(VERSION).tar.gz bundle-$(VERSION)
	@echo "Bundle: dist/bundle-$(VERSION).tar.gz ($$(du -h dist/bundle-$(VERSION).tar.gz | cut -f1))"

# Revoked licences stop working on a server once it upgrades to a release that
# carries them. Download the list from the SaaS console (On-Premise Licences ->
# Revocation list) before every release.
revocations:
	@if [ -f apps/licensing/revocations.lic ]; then \
	  echo "Revocation list: apps/licensing/revocations.lic ($$(date -r apps/licensing/revocations.lic +%Y-%m-%d))"; \
	else \
	  echo "WARNING: no apps/licensing/revocations.lic - revoked licences will keep working on servers running this release."; \
	  echo "         Download it from SaaS console -> On-Premise Licences -> Revocation list."; \
	fi

clean:
	rm -rf dist
