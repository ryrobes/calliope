-- Live mutating source fixture (MariaDB dialect). The databases below are kept
-- fresh by the rvbbit-sample-live feeder from public feeds (Citi Bike GBFS,
-- USGS earthquakes, NYC 311) plus a chaos schema whose churn is steerable at
-- runtime, so the appliance mirror path sees inserts, updates, deletes,
-- revisions, retention deletes, duplicate keys and schema drift on a real
-- cadence instead of static snapshots.
--
-- Two principals, both test-only:
--   feeder        — read/write on the four live databases (the feeder only)
--   mirror_reader — SELECT only, what the appliance's dlt mirror authenticates as

CREATE DATABASE IF NOT EXISTS bikeshare CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS quakes    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS nyc311    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS chaos     CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'feeder'@'%' IDENTIFIED BY 'rvbbit_feeder';
GRANT ALL PRIVILEGES ON bikeshare.* TO 'feeder'@'%';
GRANT ALL PRIVILEGES ON quakes.*    TO 'feeder'@'%';
GRANT ALL PRIVILEGES ON nyc311.*    TO 'feeder'@'%';
GRANT ALL PRIVILEGES ON chaos.*     TO 'feeder'@'%';

CREATE USER IF NOT EXISTS 'mirror_reader'@'%' IDENTIFIED BY 'mirror_readonly';
GRANT SELECT ON bikeshare.* TO 'mirror_reader'@'%';
GRANT SELECT ON quakes.*    TO 'mirror_reader'@'%';
GRANT SELECT ON nyc311.*    TO 'mirror_reader'@'%';
GRANT SELECT ON chaos.*     TO 'mirror_reader'@'%';

FLUSH PRIVILEGES;
