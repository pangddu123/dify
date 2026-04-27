"""Console API: ``GET /workspaces/current/local-models``.

Surface for the parallel-ensemble node's *model* dropdown (P2.11). Each
entry is a :class:`BackendInfo` projection of one yaml registry row —
``{id, backend, model_name, capabilities, metadata}`` — with **no
url / api_key / api_key_env**. The omission is the workspace-side half
of the T2 SSRF / credential boundary: even if the frontend leaks the
response into a workflow DSL, no secret travels.

Reads :class:`ModelRegistry.instance()` directly. The registry is the
authoritative source the node will consume at runtime; routing through
a service layer would just reflect the same call.
"""

from flask_restx import Resource, fields

from controllers.console import console_ns
from controllers.console.wraps import account_initialization_required, setup_required
from core.workflow.nodes.parallel_ensemble.registry import ModelRegistry
from libs.login import login_required


@console_ns.route("/workspaces/current/local-models")
class LocalModelsApi(Resource):
    @console_ns.doc("list_local_models")
    @console_ns.doc(description="List parallel-ensemble model aliases (BackendInfo, no url/api_key).")
    @console_ns.response(
        200,
        "Success",
        fields.List(fields.Raw(description="BackendInfo: id, backend, model_name, capabilities, metadata")),
    )
    @setup_required
    @login_required
    @account_initialization_required
    def get(self):
        return {"models": list(ModelRegistry.instance().list_aliases())}
