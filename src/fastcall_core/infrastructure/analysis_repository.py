from sqlalchemy import select

from fastcall_core.infrastructure.outbound_orm import CallAnalysisModel


class SqlAlchemyCallAnalysisRepository:
    def __init__(self, session):
        self.session = session

    def upsert(self, analysis_id, call_id, business_result, analysis, now):
        model = self.session.execute(
            select(CallAnalysisModel).where(CallAnalysisModel.call_id == call_id)
        ).scalar_one_or_none()
        if model is None:
            model = CallAnalysisModel(
                id=analysis_id, call_id=call_id, business_result=business_result,
                analysis_json=analysis, created_at=now, updated_at=now,
            )
            self.session.add(model)
        else:
            model.business_result = business_result
            model.analysis_json = analysis
            model.updated_at = now
        self.session.flush()
        return model
