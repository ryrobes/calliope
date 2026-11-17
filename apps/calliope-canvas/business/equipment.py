"""Small fictional equipment register shared by quotes and their service visits."""
def seed(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS counter_equipment (
        id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, name TEXT NOT NULL,
        location TEXT NOT NULL, model TEXT NOT NULL, serial TEXT NOT NULL,
        installed TEXT NOT NULL, service_notes TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS counter_quote_equipment (
        quote_id TEXT PRIMARY KEY REFERENCES counter_quotes(id),
        equipment_id TEXT NOT NULL REFERENCES counter_equipment(id));
    ''')
    fixtures=[
      ('EQ-CEDAR-1','cedar','Heat pump · 3 ton','East side pad','Demo HP36','DEMO-CEDAR-036','2021-06-14','Verify matched indoor coil and electrical rating before ordering replacements.'),
      ('EQ-CEDAR-2','cedar','Air handler · 3 ton','Utility closet','Demo AH36','DEMO-CEDAR-A36','2021-06-14','Filter access is inside the utility closet.'),
      ('EQ-HARBOR-1','harbor','Rooftop unit · 7.5 ton','Roof / north zone','Demo RT90','DEMO-HARBOR-090','2018-04-20','Roof access through side gate; arrange ladder access with the customer.'),
      ('EQ-OAK-1','oak','Split system · 2 ton','Building A / unit 2','Demo AC24','DEMO-OAK-024','2023-08-11','Confirm tenant access before dispatch.')]
    db.executemany('INSERT OR IGNORE INTO counter_equipment VALUES (?,?,?,?,?,?,?,?)',fixtures)
    # Seed a default relationship only for the known demo quotes. Existing orders
    # and visits remain unchanged; equipment descriptions are copied on new visits.
    db.executemany('INSERT OR IGNORE INTO counter_quote_equipment VALUES (?,?)',[(q,e) for q,e in [('Q-1042','EQ-HARBOR-1'),('Q-1043','EQ-CEDAR-1')] if db.execute('SELECT 1 FROM counter_quotes WHERE id=?',(q,)).fetchone()])

def for_quote(db,quote_id):
    row=db.execute('SELECT e.* FROM counter_equipment e JOIN counter_quote_equipment q ON q.equipment_id=e.id WHERE q.quote_id=?',(quote_id,)).fetchone()
    return dict(row) if row else None

def for_visit(db,visit_id):
    row=db.execute('SELECT quote_id FROM counter_orders WHERE visit_id=?',(visit_id,)).fetchone()
    return for_quote(db,row['quote_id']) if row else None
