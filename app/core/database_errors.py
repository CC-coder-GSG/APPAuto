from sqlalchemy.exc import TimeoutError


def is_database_busy(exc):
    original = getattr(exc, 'orig', None)
    code = getattr(original, 'sqlstate', None) or getattr(original, 'pgcode', None)
    # No automatic replay: a request may already have changed a remote task.
    return (isinstance(exc, TimeoutError)
            or 'database is locked' in str(original).lower()
            or code in {'40001', '40P01', '55P03', '57014', '53300', '57P01', '57P02', '57P03'}
            or bool(code and code.startswith('08')))
