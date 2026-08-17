from sqlmodel import Session, SQLModel, create_engine

from src.database.models.working_hours_model import WorkingHours
from src.services.availability_service import _get_hours_for_employee


def _make_session() -> Session:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine, tables=[WorkingHours.__table__])
    return Session(engine)


def test_employee_disabled_day_does_not_fallback_to_business_hours():
    session = _make_session()

    # Business-wide Saturday enabled
    session.add(
        WorkingHours(
            business_id="b1",
            day_of_week=5,
            start_time="09:00",
            end_time="18:00",
            enabled=True,
            member_user_id=None,
        )
    )

    # Employee-specific Saturday explicitly disabled
    session.add(
        WorkingHours(
            business_id="b1",
            day_of_week=5,
            start_time="09:00",
            end_time="18:00",
            enabled=False,
            member_user_id="u1",
        )
    )
    session.commit()

    blocks = _get_hours_for_employee(session, "b1", 5, "u1")

    # Explicit disabled employee day must stay closed (no fallback)
    assert blocks == []


def test_employee_enabled_blocks_take_priority_over_business_hours():
    session = _make_session()

    session.add(
        WorkingHours(
            business_id="b1",
            day_of_week=1,
            start_time="09:00",
            end_time="18:00",
            enabled=True,
            member_user_id=None,
        )
    )
    session.add(
        WorkingHours(
            business_id="b1",
            day_of_week=1,
            start_time="10:00",
            end_time="15:00",
            enabled=True,
            member_user_id="u1",
        )
    )
    session.commit()

    blocks = _get_hours_for_employee(session, "b1", 1, "u1")

    assert len(blocks) == 1
    assert blocks[0].start_time == "10:00"
    assert blocks[0].end_time == "15:00"
    assert blocks[0].member_user_id == "u1"


def test_fallback_to_business_hours_when_employee_has_no_rows_for_day():
    session = _make_session()

    session.add(
        WorkingHours(
            business_id="b1",
            day_of_week=2,
            start_time="09:00",
            end_time="18:00",
            enabled=True,
            member_user_id=None,
        )
    )
    session.commit()

    blocks = _get_hours_for_employee(session, "b1", 2, "u1")

    assert len(blocks) == 1
    assert blocks[0].member_user_id is None
