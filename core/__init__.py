"""kira_ops internal helpers: permission, backup, audit, confirm, redact, paths.

Framework-independent on purpose - importing these modules never touches the
plugin loader, so they keep working while other parts of the plugin reload.
"""
