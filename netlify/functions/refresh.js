exports.handler = async function(event) {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method not allowed' };
  }

  const authHeader = event.headers['x-refresh-secret'] || '';
  const expectedSecret = process.env.REFRESH_SECRET || '';
  if (!expectedSecret || authHeader !== expectedSecret) {
    return { statusCode: 401, body: JSON.stringify({ error: 'Unauthorized' }) };
  }

  const ghPat = process.env.GH_PAT || '';
  if (!ghPat) {
    return { statusCode: 500, body: JSON.stringify({ error: 'GH_PAT not configured' }) };
  }

  const response = await fetch(
    'https://api.github.com/repos/GBB94/Teachable-Sales-Intelligence/actions/workflows/refresh-performance.yml/dispatches',
    {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${ghPat}`,
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main', inputs: { days: '90' } }),
    }
  );

  if (response.status === 204) {
    return {
      statusCode: 200,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        status: 'triggered',
        message: 'Refresh started \u2014 data will update in ~2 minutes. Reload when ready.',
      }),
    };
  }

  const errorText = await response.text();
  console.error('GitHub API error:', response.status, errorText);
  return {
    statusCode: 502,
    body: JSON.stringify({ error: 'Failed to trigger workflow', detail: errorText }),
  };
};
