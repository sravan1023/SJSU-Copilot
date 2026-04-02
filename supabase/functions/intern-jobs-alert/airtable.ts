import type { AirtableJobRow } from './types.ts';

const TOP_LIMIT = 100;

interface FetchAirtableOptions {
  sharedViewUrl: string;
  apiToken?: string;
  baseId?: string;
  table?: string;
  view?: string;
}

interface AirtableColumn {
  id: string;
  name: string;
}

interface AirtableSharedViewPayload {
  data?: {
    table?: {
      columns?: AirtableColumn[];
      rows?: Array<{
        id: string;
        cellValuesByColumnId?: Record<string, unknown>;
      }>;
    };
  };
}

interface AirtableApiListResponse {
  records?: Array<{
    id: string;
    fields?: Record<string, unknown>;
  }>;
  offset?: string;
}

function extractAccessPolicyFromHtml(html: string): string | null {
  const urlMatch = html.match(/accessPolicy=([^&"']+)/);
  if (urlMatch?.[1]) {
    try {
      return decodeURIComponent(urlMatch[1]);
    } catch {
      // Ignore and try other extraction paths.
    }
  }

  const jsonMatch = html.match(/"accessPolicy":"(.*?)"/);
  if (jsonMatch?.[1]) {
    const raw = jsonMatch[1];
    try {
      return JSON.parse(`"${raw}"`) as string;
    } catch {
      return raw;
    }
  }

  return null;
}

function extractViewId(accessPolicy: string): string | null {
  const match = accessPolicy.match(/viw[a-zA-Z0-9]+/);
  return match?.[0] ?? null;
}

function extractApplicationId(accessPolicy: string): string | null {
  const match = accessPolicy.match(/"applicationId":"(app[a-zA-Z0-9]+)"/);
  return match?.[1] ?? null;
}

function parseShareId(sharedViewUrl: string): string {
  const match = sharedViewUrl.match(/\/shr[a-zA-Z0-9]+/);
  if (!match) {
    throw new Error('Airtable shared view URL must include a share id like /shrXXXXXXXXXXXXXX.');
  }

  return match[0].slice(1);
}

function normalizeEntriesToRow(
  sourceRecordId: string,
  entries: Array<{ key: string; name: string; value: string; raw: unknown }>,
): AirtableJobRow {
  const firstUrlLike =
    entries.find((entry) => /url|link|apply/.test(entry.key) && /^https?:\/\//i.test(entry.value)) ??
    entries.find((entry) => /^https?:\/\//i.test(entry.value));

  const firstCompanyLike =
    entries.find((entry) => /company|employer|organization/.test(entry.key) && entry.value.length > 0) ??
    entries.find((entry) => /company/.test(entry.key) && entry.value.length > 0);

  const firstTitleLike =
    entries.find((entry) => /title|role|position|job/.test(entry.key) && entry.value.length > 0) ??
    entries.find((entry) => entry.value.length > 0 && entry !== firstCompanyLike && entry !== firstUrlLike);

  if (!firstUrlLike?.value || !firstCompanyLike?.value || !firstTitleLike?.value) {
    throw new Error(`Unable to parse required Airtable fields for record ${sourceRecordId}.`);
  }

  return {
    sourceRecordId,
    jobUrl: firstUrlLike.value,
    company: firstCompanyLike.value,
    title: firstTitleLike.value,
    rawRecord: Object.fromEntries(entries.map((entry) => [entry.name, entry.raw ?? entry.value])),
  };
}

function normalizeCellValue(rawValue: unknown): string {
  if (rawValue == null) {
    return '';
  }

  if (typeof rawValue === 'string' || typeof rawValue === 'number' || typeof rawValue === 'boolean') {
    return String(rawValue).trim();
  }

  if (Array.isArray(rawValue)) {
    const parts = rawValue
      .map((item) => {
        if (typeof item === 'string') {
          return item;
        }
        if (item && typeof item === 'object') {
          const record = item as Record<string, unknown>;
          const firstCandidate =
            (record.text as string | undefined) ??
            (record.url as string | undefined) ??
            (record.label as string | undefined) ??
            (record.name as string | undefined) ??
            (record.value as string | undefined);
          return firstCandidate ?? '';
        }
        return '';
      })
      .filter(Boolean);

    return parts.join(' ').trim();
  }

  if (typeof rawValue === 'object') {
    const record = rawValue as Record<string, unknown>;
    const firstCandidate =
      (record.text as string | undefined) ??
      (record.url as string | undefined) ??
      (record.label as string | undefined) ??
      (record.name as string | undefined) ??
      (record.value as string | undefined);

    return firstCandidate ? String(firstCandidate).trim() : '';
  }

  return '';
}

function parseRecord(
  row: { id: string; cellValuesByColumnId?: Record<string, unknown> },
  columns: AirtableColumn[],
): AirtableJobRow {
  const valuesByColumnId = row.cellValuesByColumnId ?? {};

  const flattened = columns.map((column) => {
    const raw = valuesByColumnId[column.id];
    return {
      key: column.name.toLowerCase(),
      name: column.name,
      value: normalizeCellValue(raw),
      raw,
    };
  });

  return normalizeEntriesToRow(row.id, flattened);
}

function parseApiRecord(record: { id: string; fields?: Record<string, unknown> }): AirtableJobRow {
  const fields = record.fields ?? {};
  const entries = Object.entries(fields).map(([name, raw]) => ({
    key: name.toLowerCase(),
    name,
    value: normalizeCellValue(raw),
    raw,
  }));

  return normalizeEntriesToRow(record.id, entries);
}

async function fetchViaOfficialApi(opts: Required<Pick<FetchAirtableOptions, 'apiToken' | 'baseId' | 'table' | 'view'>>): Promise<AirtableJobRow[]> {
  let offset: string | undefined;
  const rows: AirtableJobRow[] = [];

  while (rows.length < TOP_LIMIT) {
    const url = new URL(`https://api.airtable.com/v0/${opts.baseId}/${encodeURIComponent(opts.table)}`);
    url.searchParams.set('view', opts.view);
    url.searchParams.set('pageSize', String(Math.min(100, TOP_LIMIT - rows.length)));
    if (offset) {
      url.searchParams.set('offset', offset);
    }

    const response = await fetch(url.toString(), {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${opts.apiToken}`,
        Accept: 'application/json',
      },
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Airtable API request failed (${response.status}): ${body.slice(0, 180)}`);
    }

    const payload = (await response.json()) as AirtableApiListResponse;
    const batch = (payload.records ?? []).map(parseApiRecord);
    rows.push(...batch);

    if (!payload.offset || batch.length === 0) {
      break;
    }

    offset = payload.offset;
  }

  return rows.slice(0, TOP_LIMIT);
}

async function fetchViaSharedView(sharedViewUrl: string): Promise<AirtableJobRow[]> {
  const shareId = parseShareId(sharedViewUrl);

  const sharedPageResponse = await fetch(sharedViewUrl, {
    method: 'GET',
    headers: {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    },
  });

  if (!sharedPageResponse.ok) {
    throw new Error(`Airtable shared view page request failed with status ${sharedPageResponse.status}.`);
  }

  const sharedPageHtml = await sharedPageResponse.text();
  const accessPolicy = extractAccessPolicyFromHtml(sharedPageHtml);

  const objectParams = encodeURIComponent(
    JSON.stringify({
      shouldUseNestedResponseFormat: true,
    }),
  );

  const requestId = `req${Math.random().toString(36).slice(2, 8)}`;
  const viewId = accessPolicy ? extractViewId(accessPolicy) : null;
  const applicationId = accessPolicy ? extractApplicationId(accessPolicy) : null;

  const params = new URLSearchParams({
    stringifiedObjectParams: JSON.stringify({ shouldUseNestedResponseFormat: true }),
  });

  if (accessPolicy) {
    params.set('requestId', requestId);
    params.set('accessPolicy', accessPolicy);
  }

  const endpoint = `https://airtable.com/v0.3/view/${viewId ?? shareId}/readSharedViewData?${params.toString()}`;

  const response = await fetch(endpoint, {
    method: 'GET',
    headers: {
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      Accept: 'application/json',
      ...(applicationId ? { 'x-airtable-application-id': applicationId } : {}),
      ...(accessPolicy
        ? {
            'x-requested-with': 'XMLHttpRequest',
            'x-user-locale': 'en',
            'x-time-zone': 'America/Los_Angeles',
          }
        : {}),
    },
  });

  if (!response.ok) {
    throw new Error(`Airtable request failed with status ${response.status}.`);
  }

  const rawBody = await response.text();
  let payload: AirtableSharedViewPayload;

  try {
    payload = JSON.parse(rawBody) as AirtableSharedViewPayload;
  } catch {
    throw new Error(
      'Airtable shared view returned non-JSON content. Shared links may be blocked/expired. Configure AIRTABLE_API_TOKEN + AIRTABLE_BASE_ID + AIRTABLE_TABLE + AIRTABLE_VIEW for reliable fetch.',
    );
  }

  const columns = payload.data?.table?.columns ?? [];
  const rows = payload.data?.table?.rows ?? [];

  if (rows.length === 0) {
    return [];
  }

  const parsedRows = rows.slice(0, TOP_LIMIT).map((row) => parseRecord(row, columns));
  return parsedRows;
}

export async function fetchTopAirtableJobs(opts: FetchAirtableOptions): Promise<AirtableJobRow[]> {
  if (opts.sharedViewUrl.includes('shrXXXXXXXXXXXXXX')) {
    throw new Error('AIRTABLE_SHARED_VIEW_URL is still placeholder text. Set your real shared view URL.');
  }

  if (opts.apiToken && opts.baseId && opts.table && opts.view) {
    return fetchViaOfficialApi({
      apiToken: opts.apiToken,
      baseId: opts.baseId,
      table: opts.table,
      view: opts.view,
    });
  }

  return fetchViaSharedView(opts.sharedViewUrl);
}
