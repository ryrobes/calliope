-- Computer browser access delegates the requesting person's identity. Older
-- sessions accidentally supplied a device label as that person's display name.
-- There is no retained original name to restore: use the existing email fallback
-- until the next human profile observation. Preserve identity, access and avatars.
UPDATE rvbbit.application_principals
   SET display_name=NULL
 WHERE display_name='Callie’s Computer';
