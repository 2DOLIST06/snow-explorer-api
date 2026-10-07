"""Per-scan physical-column inventory; never change global Peewee models."""
from collections import defaultdict

from peewee import Column, IntegerField, PostgresqlDatabase, fn


class SchemaCompatibilityError(RuntimeError):
    def __init__(self, message, findings):
        super().__init__(message)
        self.findings = findings


class PhysicalSchema:
    def __init__(self, database, models):
        self.columns = defaultdict(set)
        self.types = defaultdict(dict)
        self.raw_numeric_fields = set()
        self.findings = []
        if isinstance(database, PostgresqlDatabase):
            # Resolve the same quoted relation names/search_path as Peewee.
            # One catalogue SELECT covers all tables, without loading data rows.
            entries, params = [], []
            for model in models:
                table = model._meta.table_name
                parts = [model._meta.schema, table] if model._meta.schema else [table]
                qualified = ".".join('"' + part.replace('"', '""') + '"' for part in parts)
                entries.append("(%s, %s)")
                params.extend((table, qualified))
            sql = (
                "WITH scanned_tables(table_name, relation_name) AS (VALUES " + ", ".join(entries) + ") "
                "SELECT s.table_name, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod) "
                "FROM scanned_tables s "
                "JOIN pg_catalog.pg_attribute a ON a.attrelid = pg_catalog.to_regclass(s.relation_name) "
                "WHERE a.attnum > 0 AND NOT a.attisdropped ORDER BY s.table_name, a.attnum"
            )
            for table, column, data_type in database.execute_sql(sql, params).fetchall():
                self.columns[table].add(column)
                self.types[table][column] = data_type
        else:
            # Isolated SQLite fixtures use Peewee's read-only PRAGMA inventory.
            for model in models:
                table = model._meta.table_name
                for column in database.get_columns(table, model._meta.schema):
                    self.columns[table].add(column.name)
                    self.types[table][column.name] = column.data_type
        for model in models:
            table = model._meta.table_name
            actual = self.columns[table]
            if not actual:
                self.findings.append({"table": table, "code": "database_table_unavailable", "severity": "error"})
                continue
            expected = {field.column_name for field in model._meta.fields.values()}
            for field in model._meta.sorted_fields:
                if field.column_name not in actual:
                    self.findings.append({"table": table, "field": field.name, "column": field.column_name,
                                          "code": "model_column_missing_in_database", "severity": "warning"})
                else:
                    data_type = self.types[table][field.column_name]
                    base_type = data_type.lower().split("(", 1)[0].strip()
                    if isinstance(field, IntegerField) and base_type in {
                            "real", "double precision", "float", "numeric", "decimal"}:
                        # Peewee's integer conversion would truncate physical
                        # fractional values. Preserve the driver's real value.
                        self.raw_numeric_fields.add((model, field.name))
                        self.findings.append({"table": table, "field": field.name, "column": field.column_name,
                                              "code": "model_column_type_mismatch", "severity": "warning",
                                              "model_type": "integer", "database_type": data_type})
                    elif field.field_type == "TIMESTAMPTZ" and base_type == "timestamp without time zone":
                        self.findings.append({"table": table, "field": field.name, "column": field.column_name,
                                              "code": "model_column_type_mismatch", "severity": "info",
                                              "model_type": "timestamp with time zone", "database_type": data_type,
                                              "timestamp_convention": "legacy_naive_assumed_utc"})
            for column in sorted(actual - expected):
                self.findings.append({"table": table, "field": column,
                                      "code": "database_column_not_in_model", "severity": "info"})

    def has(self, model, field):
        return model._meta.fields[field].column_name in self.columns[model._meta.table_name]

    def require(self, model, *fields):
        missing = [field for field in fields if not self.has(model, field)]
        if missing:
            raise SchemaCompatibilityError(
                f"Required scan columns unavailable in {model._meta.table_name}: {', '.join(missing)}",
                self.findings,
            )

    def projection(self, model, content_fields=(), extra_fields=(), extra_content_fields=()):
        actual = self.columns[model._meta.table_name]
        fields = [field.coerce(False) if (model, name) in self.raw_numeric_fields else field
                  for name, field in model._meta.fields.items()
                  if name not in content_fields and field.column_name in actual]
        for name in extra_fields:
            if name in actual and name not in model._meta.columns:
                fields.append(Column(model._meta.table, name).alias(name))
        contents = [(name, model._meta.fields[name]) for name in content_fields if self.has(model, name)]
        contents.extend((name, Column(model._meta.table, name)) for name in extra_content_fields if name in actual)
        for name, field in contents:
            # Do not inherit TextField conversion for numeric LENGTH results.
            fields.extend((fn.LENGTH(field).coerce(False).alias(name + "__length"),
                           fn.MD5(field).alias(name + "__md5"),
                           (fn.LENGTH(fn.TRIM(field)).coerce(False) > 0).alias(name + "__present")))
        if not fields:
            raise SchemaCompatibilityError(f"No readable scan columns in {model._meta.table_name}", self.findings)
        return model.select(*fields).dicts()
