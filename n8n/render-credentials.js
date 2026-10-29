// Writes the n8n credentials file from environment variables at container start.
// Nothing secret is stored in the repo: values come from .env (or the demo defaults
// in docker-compose.yml, which only point at the local demo containers).
const env = process.env;

const credentials = [
  {
    id: 'pgSalesDb0000001',
    name: 'Sales database (Postgres)',
    type: 'postgres',
    data: {
      host: env.DB_HOST || 'postgres',
      port: Number(env.DB_PORT || 5432),
      database: env.DB_NAME || 'sales',
      user: env.DB_USER || 'sales',
      password: env.DB_PASSWORD || '',
      ssl: env.DB_SSL || 'disable', // Supabase: "require"
      allowUnauthorizedCerts: false,
    },
  },
  {
    id: 'smtpDemoMail0001',
    name: 'Demo mailbox (Mailpit SMTP)',
    type: 'smtp',
    data: {
      host: env.SMTP_HOST || 'mailpit',
      port: Number(env.SMTP_PORT || 1025),
      secure: env.SMTP_SECURE === 'true',
      disableStartTls: env.SMTP_SECURE !== 'true',
      user: env.SMTP_USER || '',
      password: env.SMTP_PASSWORD || '',
    },
  },
];

// Empty placeholder for the real Outlook mailbox. It is NOT connected in the demo: to use
// it, open this credential in n8n, type your Microsoft app's client ID and secret and
// click "Connect my account" (see README). Imported only once, so what you enter in n8n
// is never overwritten by a restart.
const outlook = [
  {
    id: 'outlookNotConn01',
    name: 'Microsoft Outlook (NOT connected in the demo)',
    type: 'microsoftOutlookOAuth2Api',
    data: { clientId: '', clientSecret: '' },
  },
];

const which = process.argv[2] === 'outlook' ? outlook : credentials;
process.stdout.write(JSON.stringify(which, null, 2));
