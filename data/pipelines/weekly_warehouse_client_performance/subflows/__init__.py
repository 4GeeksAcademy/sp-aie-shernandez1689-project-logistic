"""Subflows del pipeline de desempeño semanal.

Cada subflow se ejecuta como un ``@flow`` de Prefect con inputs y outputs
explícitos (Pydantic), sin depender de variables globales entre etapas.
"""

from data.pipelines.weekly_warehouse_client_performance.subflows.extract_flow import (
    extract_subflow,
)
from data.pipelines.weekly_warehouse_client_performance.subflows.transform_flow import (
    transform_subflow,
)
from data.pipelines.weekly_warehouse_client_performance.subflows.load_flow import (
    load_subflow,
)
from data.pipelines.weekly_warehouse_client_performance.subflows.export_flow import (
    export_eval_snapshot_subflow,
)
from data.pipelines.weekly_warehouse_client_performance.subflows.csv_backup_flow import (
    csv_backup_subflow,
)
from data.pipelines.weekly_warehouse_client_performance.subflows.models import (
    CsvBackupInput,
    CsvBackupOutput,
    ExportInput,
    ExportOutput,
    ExtractInput,
    ExtractOutput,
    LoadInput,
    LoadOutput,
    TransformInput,
    TransformOutput,
)

__all__ = [
    "extract_subflow",
    "transform_subflow",
    "load_subflow",
    "export_eval_snapshot_subflow",
    "csv_backup_subflow",
    "CsvBackupInput",
    "CsvBackupOutput",
    "ExportInput",
    "ExportOutput",
    "ExtractInput",
    "ExtractOutput",
    "LoadInput",
    "LoadOutput",
    "TransformInput",
    "TransformOutput",
]
