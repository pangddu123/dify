"""Console API: ``GET /workspaces/current/aggregators``.

Surface for the parallel-ensemble node's *aggregator* dropdown (P2.11).
Each entry projects an :class:`Aggregator` subclass into:

    {name, i18n_key_prefix, ui_schema, config_schema, scope}

The frontend filters this list so only aggregators whose ``scope``
matches the currently selected runner's ``aggregator_scope`` are
shown — the pairing rule is enforced server-side at run time, this
endpoint just supplies the metadata the UI needs to do the filter.
"""

from typing import Any

from flask_restx import Resource, fields

from controllers.console import console_ns
from controllers.console.wraps import account_initialization_required, setup_required
from core.workflow.nodes.parallel_ensemble.registry import AggregatorRegistry
from core.workflow.nodes.parallel_ensemble.spi.aggregator import Aggregator
from libs.login import login_required


def _project_aggregator(agg_cls: type[Aggregator[Any, Any, Any, Any]]) -> dict[str, Any]:
    return {
        "name": agg_cls.name,
        "i18n_key_prefix": agg_cls.i18n_key_prefix,
        "ui_schema": agg_cls.ui_schema,
        "config_schema": agg_cls.config_schema_json(),
        "scope": agg_cls.scope,
    }


@console_ns.route("/workspaces/current/aggregators")
class AggregatorsApi(Resource):
    @console_ns.doc("list_aggregators")
    @console_ns.doc(description="List registered aggregators with scope + ui_schema.")
    @console_ns.response(
        200,
        "Success",
        fields.List(fields.Raw(description="Aggregator descriptor")),
    )
    @setup_required
    @login_required
    @account_initialization_required
    def get(self):
        aggregators = [
            _project_aggregator(AggregatorRegistry.get(name)) for name in AggregatorRegistry.known_aggregators()
        ]
        return {"aggregators": aggregators}
