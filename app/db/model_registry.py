"""Load every model before schema inspection or migration comparison."""
import importlib
import pkgutil


def metadata():
    import app.models
    from app.db.base import Base
    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f'app.models.{module.name}')
    # Remain immediate for normal requests; offline imports may defer cyclic FKs.
    for table in Base.metadata.tables.values():
        for constraint in table.foreign_key_constraints:
            constraint.deferrable = True
            constraint.initially = 'IMMEDIATE'
    return Base.metadata
