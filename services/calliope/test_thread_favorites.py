"""Personal favorites persist without mutating the conversation or bypassing visibility."""
import pytest
import floor_data
import calliope_access
from test_workspace_sql import db, OWNER, SESSION  # noqa:F401


def test_personal_favorites_are_idempotent_authorized_and_do_not_touch_activity(db):
    with db() as c:
        c.execute("""ALTER TABLE rvbbit.calliope_sessions ADD COLUMN updated_at timestamptz DEFAULT '2020-01-01';
          CREATE TABLE rvbbit.application_principals(email text,display_name text,avatar_key uuid);
          CREATE TABLE rvbbit.calliope_session_view_grants(session_id uuid,principal_email text);
          CREATE FUNCTION rvbbit.calliope_session_can_view(uuid,text,boolean) RETURNS boolean LANGUAGE sql AS $$
            SELECT EXISTS(SELECT 1 FROM rvbbit.calliope_sessions s WHERE s.id=$1 AND
              (lower(s.owner_email)=lower($2) OR EXISTS(SELECT 1 FROM rvbbit.calliope_session_view_grants g
               WHERE g.session_id=s.id AND g.principal_email=lower($2))))$$;""")
        c.execute("CREATE FUNCTION rvbbit.calliope_session_role(uuid,text) RETURNS text LANGUAGE sql AS $$ SELECT CASE WHEN lower(owner_email)=lower($2) THEN 'owner' ELSE 'viewer' END FROM rvbbit.calliope_sessions WHERE id=$1 $$")
        c.execute('INSERT INTO rvbbit.calliope_session_view_grants VALUES(%s,%s)', (SESSION,'reader@example.com'))
        before=c.execute('SELECT updated_at FROM rvbbit.calliope_sessions').fetchone()['updated_at']
    for _ in range(2):
        assert floor_data.set_favorite(db,OWNER.upper(),SESSION,True)=={'favorite':True}
    assert floor_data.favorite_sessions(db,OWNER)==[SESSION]
    assert floor_data.favorite_sessions(db,'reader@example.com')==[]
    floor_data.set_favorite(db,'reader@example.com',SESSION,True)
    floor_data.set_favorite(db,OWNER,SESSION,False)
    assert floor_data.favorite_sessions(db,OWNER)==[]
    assert floor_data.favorite_sessions(db,'reader@example.com')==[SESSION]
    with pytest.raises(calliope_access.CalliopeAccessError):
        floor_data.set_favorite(db,'outsider@example.com',SESSION,True)
    with pytest.raises(ValueError):floor_data.set_favorite(db,OWNER,SESSION,'true')
    with db() as c:
        assert c.execute('SELECT updated_at FROM rvbbit.calliope_sessions').fetchone()['updated_at']==before
        assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_session_favorites').fetchone()['n']==1
        c.execute('DELETE FROM rvbbit.calliope_sessions WHERE id=%s', (SESSION,))
    assert floor_data.favorite_sessions(db,'reader@example.com')==[]
