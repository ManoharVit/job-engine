from sqlalchemy.orm import declarative_base
from sqlalchemy.types import TypeDecorator, Text
import json

Base = declarative_base()

class JSONList(TypeDecorator):
    impl = Text

    def process_bind_param(self, value, dialect):
        if value is None:
            return '[]'
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if not value:
            return []
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return []
