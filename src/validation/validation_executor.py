"""
Validation executor for batch processing
Orchestrates execution of count and data validations

Every batch reports column coverage alongside the pass rate. A run that
compared 6 of 9 columns says so, next to the result, so a green summary can
never be mistaken for a complete one.

Enhanced (Steps 1-3):
  - Step 1: SkipClassifier     — every skipped column is JUSTIFIED or UNJUSTIFIED
  - Step 2: SkipAwareCLIReporter — CLI shows skipped column detail in two sections
  - Step 3: TablePresenceChecker — source tables checked against target BEFORE validation;
            missing table with no exclusion config = CRITICAL FAIL, not a silent skip
"""
import yaml
import os
from pathlib import Path
from src.utils.runid import generate_runid
from src.utils.logging_config import get_logger, add_file_handler
from src.utils.path_manager import get_config_output_paths
from src.db.factory import DatabaseFactory
from src.validation.count_validator import execute_count_validation
from src.validation.data_validator import execute_data_validation
from src.validation.config_schema import validate_config_file
from src.core.skip_classifier import SkipClassifier
from src.core.skip_aware_cli_reporter import SkipAwareCLIReporter
from src.core.table_presence_checker import TablePresenceChecker, TablePresenceResult

logger = get_logger(__name__)


class ValidationExecutor:
    """Orchestrates batch validation execution"""

    def __init__(self, base_dir=None, environment="dev"):
        """
        Initialize validation executor

        Args:
            base_dir:    Base project directory (contains config/, output/, src/)
            environment: Environment name ('dev', 'uat', 'prod') for .env selection
        """
        repo_root = Path(__file__).resolve().parents[2]
        requested_base = Path(base_dir).resolve() if base_dir else repo_root
        # Running from src/ should still write reports to repository/output/.
        self.base_dir = str(repo_root if requested_base.name.lower() == "src" else requested_base)
        self.environment = environment
        self.db_factory = DatabaseFactory()
        self.run_id, self.run_at = generate_runid()

        # Steps 1-3 components
        exclusions_yaml = os.path.join(self.base_dir, "config", "exclusions.yaml")
        self._skip_classifier   = SkipClassifier()
        self._cli_reporter      = SkipAwareCLIReporter(use_color=True)
        self._presence_checker  = TablePresenceChecker(
            exclusions_config_path=exclusions_yaml if os.path.exists(exclusions_yaml) else None
        )

        logger.info(f"ValidationExecutor initialized with run_id: {self.run_id}")
    
    def execute_batch(
        self,
        layer: str,
        tables: list = None,
        validation_types: list = None,
        config_dir: str = None,
        source_table_list: list = None,
        target_table_list: list = None,
    ) -> dict:
        """
        Execute batch validation.

        Steps 1-3 are integrated here:
          Step 3 runs FIRST (table presence check before any query execution).
          Step 1 runs per-table (skip classification of each skipped column).
          Step 2 runs at the end (CLI report with skip details).

        Args:
            layer:             'bronze', 'silver', 'gold', or 'reporting'
            tables:            List of table names or None for all
            validation_types:  ['count_validation', 'data_validation'] or subset
            config_dir:        Path to config directory (default: base_dir/config)
            source_table_list: Optional explicit list of source tables for Step 3
                               presence check. If provided, missing tables = FAIL.
            target_table_list: Optional explicit list of target tables for Step 3
                               presence check.

        Returns:
            dict: Results of all validations executed

        Example:
            >>> executor = ValidationExecutor(base_dir='c:/project')
            >>> results = executor.execute_batch(
            ...     layer='bronze',
            ...     tables=['users', 'orders'],
            ...     validation_types=['count_validation', 'data_validation'],
            ...     config_dir='c:/project/config',
            ...     source_table_list=['users', 'orders', 'products'],
            ...     target_table_list=['USERS', 'ORDERS'],  # products MISSING → FAIL
            ... )
            >>> for result in results.values():
            ...     print(f"{result['table']}: {result['status']}")
        """
        
        if config_dir is None:
            config_dir = os.path.join(self.base_dir, "config")

        if validation_types is None:
            validation_types = ['count_validation', 'data_validation']

        logger.info(f"Starting batch validation: layer={layer}, run_id={self.run_id}")

        results = {}   # initialised here so Step 3 can inject FAIL entries early

        # ── STEP 3: TABLE PRESENCE CHECK (runs before any query) ─────────────
        presence_result: TablePresenceResult | None = None
        if source_table_list is not None and target_table_list is not None:
            logger.info("[Step 3] Running table presence check...")
            presence_result = self._presence_checker.check(
                source_tables=source_table_list,
                target_tables=target_table_list,
            )
            # Log the presence check result
            logger.info(presence_result.render_cli(use_color=False))

            if presence_result.has_critical_failures:
                critical_names = [
                    e.source_table for e in presence_result.critical_failures
                ]
                logger.error(
                    f"[Step 3] CRITICAL: {len(critical_names)} source table(s) not found "
                    f"in target: {critical_names}. These will be recorded as FAIL."
                )
                # Inject FAIL results for missing tables so they appear in summary
                for entry in presence_result.critical_failures:
                    results[f"table_presence_{entry.source_table}"] = {
                        'table':           entry.source_table,
                        'validation_type': 'table_presence',
                        'status':          'FAIL',
                        'error':           entry.message,
                        'source_count':    None,
                        'target_count':    None,
                    }
        
        # Setup paths
        output_paths, config_paths, log_path = get_config_output_paths(
            run_id=self.run_id,
            layer_type=layer,
            base_dir=self.base_dir,
            config_path=config_dir,
            validation_dirs=validation_types,
            table_list=tables or ['all']
        )
        
        # Setup logging with file handler
        os.makedirs(log_path, exist_ok=True)
        log_file = f"validation_{self.run_id}.log"
        add_file_handler(logger, log_path, log_file)
        
        # Execute validations
        for validation_type in validation_types:
            logger.info(f"\n{'='*60}")
            logger.info(f"Executing {validation_type}...")
            logger.info(f"{'='*60}")
            
            output_path = output_paths.get(validation_type)
            config_yamls = config_paths.get(validation_type, [])
            
            if not config_yamls:
                logger.warning(f"No config files found for {validation_type}")
                continue
            
            # Load and execute each config
            for config_yaml in config_yamls:
                try:
                    config_path = Path(config_yaml)
                    document, config_errors = validate_config_file(config_path)
                    if config_errors:
                        for message in config_errors:
                            logger.error(f"Invalid config: {message}")
                    if document is None:
                        logger.error(
                            f"Skipping {config_path.name} — it failed schema validation. "
                            f"Run 'python src/validate_cli.py lint' for details."
                        )
                        results[f"{validation_type}_{config_path.stem}"] = {
                            'table': config_path.stem,
                            'validation_type': validation_type,
                            'status': 'ERROR',
                            'error': '; '.join(config_errors),
                        }
                        continue

                    with open(config_yaml, 'r') as f:
                        config_data = yaml.safe_load(f)
                    
                    if not config_data:
                        logger.warning(f"Empty config: {config_yaml}")
                        continue
                    
                    configs = self._flatten_configs(config_data, validation_type)
                    
                    # Execute each validation in config
                    for config in configs:
                        table_name = config.get('source_table_name', 'unknown')
                        if tables and 'all' not in tables and table_name not in tables:
                            continue
                        result_key = f"{validation_type}_{table_name}"
                        
                        try:
                            if validation_type == 'count_validation':
                                result = execute_count_validation(
                                    config,
                                    self.db_factory,
                                    self.run_id,
                                    self.run_at,
                                    output_path
                                )
                            else:  # data_validation
                                result = execute_data_validation(
                                    config,
                                    self.db_factory,
                                    self.run_id,
                                    self.run_at,
                                    output_path
                                )
                            
                            result['table'] = table_name
                            result['validation_type'] = validation_type
                            results[result_key] = result
                        
                        except Exception as e:
                            logger.error(f"Failed to execute config for {table_name}: {e}", exc_info=True)
                            results[result_key] = {
                                'table': table_name,
                                'validation_type': validation_type,
                                'status': 'ERROR',
                                'error': str(e)
                            }
                
                except Exception as e:
                    logger.error(f"Failed to load config {config_yaml}: {e}", exc_info=True)
        
        # ── STEP 1 + 2: SKIP CLASSIFICATION + CLI REPORT ────────────────────
        logger.info(f"\n{'='*60}")
        logger.info("VALIDATION BATCH SUMMARY")
        logger.info(f"{'='*60}")

        # Load plans for skip classification (Step 1)
        plans = self._load_plans_for_results(results)

        # Print the enhanced CLI report (Steps 1 + 2 combined)
        table_skip_reports = self._cli_reporter.print_full_report(
            validation_results=results,
            plans=plans,
            presence_result=presence_result,
        )

        # Apply skip promotions: if unjustified skips found → override status to FAIL
        for skip_report in table_skip_reports:
            if skip_report.has_unjustified:
                # Find and update matching result entries
                for result in results.values():
                    if result.get('table') == skip_report.table_name:
                        if result.get('status') == 'PASS':
                            result['status'] = 'FAIL'
                            result['skip_promotion_reason'] = (
                                f"{len(skip_report.unjustified_skips)} unjustified "
                                f"skip(s) found: "
                                + ', '.join(
                                    s.column_name
                                    for s in skip_report.unjustified_skips
                                )
                            )
                            logger.warning(
                                f"[Step 1] Table '{skip_report.table_name}' PASS → FAIL: "
                                f"{result['skip_promotion_reason']}"
                            )

        # Final counts after promotion
        pass_count  = sum(1 for r in results.values() if r.get('status') == 'PASS')
        fail_count  = sum(1 for r in results.values() if r.get('status') == 'FAIL')
        error_count = sum(1 for r in results.values() if r.get('status') == 'ERROR')

        logger.info(
            f"Total: {len(results)} | PASS: {pass_count} | "
            f"FAIL: {fail_count} | ERROR: {error_count}"
        )

        # Column coverage report (existing mechanism)
        coverage = self._build_coverage_report(results)
        logger.info("")
        logger.info(coverage.render())

        logger.info(f"Run ID: {self.run_id}")
        logger.info(f"Logs: {log_path}")
        logger.info(f"Output: {self.base_dir}/output/{layer}/validation_{self.run_id}")

        return results

    def _load_plans_for_results(self, results: dict) -> dict:
        """
        Load CanonicalValidationPlans for all tables in results.

        Returns a dict of table_name → plan (or empty dict if plan store unavailable).
        Plans are used by Step 1 (SkipClassifier) to analyse skipped columns.
        """
        from src.core.plan_store import PlanStore, PlanStoreError
        store = PlanStore()
        plans = {}
        seen = set()
        for result in results.values():
            table = result.get('table')
            if not table or table in seen:
                continue
            seen.add(table)
            try:
                plan = store.load_for_table(table)
                if plan is not None:
                    plans[table] = plan
            except PlanStoreError:
                pass  # No plan available — skip analysis will show UNKNOWN
        return plans

    def _build_coverage_report(self, results: dict):
        """
        Build the column-coverage report for everything this batch touched.

        Coverage is read from the canonical plans, never from the YAML: the
        YAML is a render target and cannot say which columns were dropped or
        why. Tables with no plan are reported as UNKNOWN coverage rather than
        assumed complete — silence must not read as full coverage.
        """
        from src.core.exclusion_report import BatchExclusionReport, ExclusionReport
        from src.core.plan_store import PlanStore, PlanStoreError

        store = PlanStore()
        batch = BatchExclusionReport()
        seen = set()

        for result in results.values():
            table = result.get('table')
            if not table or table in seen:
                continue
            seen.add(table)
            try:
                plan = store.load_for_table(table)
            except PlanStoreError as exc:
                logger.warning(f"Could not read plan for '{table}': {exc}")
                plan = None

            if plan is None:
                logger.warning(
                    f"No canonical plan found for '{table}' — column coverage is UNKNOWN. "
                    f"Regenerate with 'validate_cli.py generate' so exclusions are recorded."
                )
                continue
            batch.add(ExclusionReport.from_plan(plan))

        return batch

    @staticmethod
    def _flatten_configs(config_data, validation_type):
        """Return executable validation blocks from flat or grouped YAML."""
        if isinstance(config_data, list):
            return [item for item in config_data if isinstance(item, dict)]
        if not isinstance(config_data, dict):
            raise ValueError("Validation config must be a mapping or list")

        if 'tables' not in config_data:
            return [config_data]

        configs = []
        for table_name, table_data in config_data['tables'].items():
            if not isinstance(table_data, dict):
                continue
            validation = table_data.get('validations', {}).get(validation_type)
            if validation is None:
                continue
            if isinstance(validation, list):
                configs.extend(item for item in validation if isinstance(item, dict))
            elif isinstance(validation, dict):
                configs.append(validation)
            else:
                raise ValueError(f"Invalid {validation_type} config for table {table_name}")
        return configs


if __name__ == "__main__":
    # Example usage
    executor = ValidationExecutor(
        base_dir="c:/EPAM-Personal/Migration-validator",
        environment="dev"
    )
    
    results = executor.execute_batch(
        layer="bronze",
        validation_types=['count_validation'],
        tables=['all'],
        config_dir="c:/EPAM-Personal/Migration-validator/config"
    )
    
    for result_key, result in results.items():
        print(f"\n{result_key}:")
        for k, v in result.items():
            print(f"  {k}: {v}")
