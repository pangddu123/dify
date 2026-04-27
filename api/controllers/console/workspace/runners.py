"""Console API: ``GET /workspaces/current/runners``.

Surface for the parallel-ensemble node's *runner* dropdown (P2.11).
Each entry projects an :class:`EnsembleRunner` subclass into the JSON
shape the frontend's reflective form needs:

    {
        name, i18n_key_prefix, ui_schema, config_schema,
        aggregator_scope, required_capabilities, optional_capabilities,
    }

``config_schema`` is the runner's pydantic ``model_json_schema()`` —
the frontend uses it as a fallback validator when the user types into
a field outside the ``ui_schema`` allowlist.

Capabilities are returned as ``str`` values (the enum's ``value``) so
the frontend doesn't need to know the Python ``Capability`` enum to
render a tooltip / filter dropdown.
"""

from typing import Any

from flask_restx import Resource, fields

from controllers.console import console_ns
from controllers.console.wraps import account_initialization_required, setup_required
from core.workflow.nodes.parallel_ensemble.registry import RunnerRegistry
from core.workflow.nodes.parallel_ensemble.spi.runner import EnsembleRunner
from libs.login import login_required


def _project_runner(runner_cls: type[EnsembleRunner[Any]]) -> dict[str, Any]:
    return {
        "name": runner_cls.name,
        "i18n_key_prefix": runner_cls.i18n_key_prefix,
        "ui_schema": runner_cls.ui_schema,
        "config_schema": runner_cls.config_schema_json(),
        "aggregator_scope": runner_cls.aggregator_scope,
        "required_capabilities": sorted(cap.value for cap in runner_cls.required_capabilities),
        "optional_capabilities": sorted(cap.value for cap in runner_cls.optional_capabilities),
    }


@console_ns.route("/workspaces/current/runners")
class RunnersApi(Resource):
    @console_ns.doc("list_runners")
    @console_ns.doc(description="List registered ensemble runners with i18n keys + ui_schema.")
    @console_ns.response(
        200,
        "Success",
        fields.List(fields.Raw(description="Runner descriptor")),
    )
    @setup_required
    @login_required
    @account_initialization_required
    def get(self):
        runners = [_project_runner(RunnerRegistry.get(name)) for name in RunnerRegistry.known_runners()]
        return {"runners": runners}
