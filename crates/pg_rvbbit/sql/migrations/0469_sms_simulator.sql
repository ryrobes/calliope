-- Simulated phones share the SMS pipeline, but never the real transport.
ALTER TABLE rvbbit.calliope_sms_bindings ADD COLUMN IF NOT EXISTS transport text NOT NULL DEFAULT 'twilio'
 CHECK (transport IN ('twilio','simulator'));
