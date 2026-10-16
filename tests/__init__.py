# Regression / integration suite for the ucp.yazi plugin.
#
# Run from the repository root with:
#     python3 -m unittest discover -s tests -v
#
# The suite drives a real yazi instance inside a bubblewrap sandbox with a
# read-only root filesystem, a private writable workspace, a private Xvfb
# display and a private xclip selection. It never uses the host desktop
# clipboard. Xvfb needs its own ephemeral high-display socket/lock outside
# the workspace; the plugin sees a read-only host filesystem.
