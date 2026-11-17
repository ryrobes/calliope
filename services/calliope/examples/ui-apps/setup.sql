-- Optional fictional example records. No runtime startup seeds or SQLite import.
CREATE TABLE IF NOT EXISTS calliope_apps.ui_example_jobs (
 id text PRIMARY KEY, customer text NOT NULL, status text NOT NULL,
 note text NOT NULL DEFAULT '', version integer NOT NULL DEFAULT 1
);
INSERT INTO calliope_apps.ui_example_jobs(id,customer,status,note) VALUES
 ('WO-1001','Harbor Supply','Awaiting parts','Confirm the replacement motor before scheduling.'),
 ('WO-1002','Maple School','Scheduled','Rooftop inspection is booked for Friday.'),
 ('WO-1003','Cedar Market','In progress','Technician is on site.')
ON CONFLICT (id) DO NOTHING;
