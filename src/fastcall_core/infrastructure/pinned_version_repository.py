from fastcall_core.application.records import RobotVersionRecord
from fastcall_core.infrastructure.orm import RobotAppVersionModel


class SqlAlchemyPinnedVersionRepository:
    def __init__(self, session):
        self.session = session

    def get_by_id(self, version_id):
        model = self.session.get(RobotAppVersionModel, version_id)
        if model is None:
            return None
        return RobotVersionRecord(**{
            field: getattr(model, field) for field in RobotVersionRecord.__dataclass_fields__
        })
