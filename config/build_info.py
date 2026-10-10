"""Values fixed at image build time. Dockerfile.backend.compiled rewrites
this file (before compiling it) so an on-premise image always runs in
on-premise mode — an environment variable can't switch licensing off."""

FORCED_DEPLOYMENT_MODE = None
