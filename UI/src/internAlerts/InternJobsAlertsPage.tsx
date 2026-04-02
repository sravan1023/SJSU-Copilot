import { useEffect, useMemo, useState } from 'react';
import JobsDashboardTable from './components/JobsDashboardTable';
import { fetchInternJobsDashboard, triggerInternJobsPipeline } from './lib/api';
import type { DashboardPayload } from './types/pipeline';

interface InternJobsAlertsPageProps {
  onBack: () => void;
}

const CATEGORY_DEFINITIONS = [
  { label: '💻 Software Engineering', keywords: ['software', 'engineer', 'developer', 'frontend', 'backend', 'full stack', 'mobile'] },
  { label: '📊 Data Analysis', keywords: ['data analyst', 'data analysis', 'analytics', 'analyst', 'sql'] },
  { label: '🤖 Machine Learning and AI', keywords: ['machine learning', 'ai', 'artificial intelligence', 'ml', 'data scientist'] },
  { label: '📦 Product Management', keywords: ['product manager', 'product', 'pm'] },
  { label: '💰 Accounting and Finance', keywords: ['accounting', 'finance', 'financial', 'audit'] },
  { label: '⚙️ Engineering and Development', keywords: ['engineering', 'developer', 'engineer', 'devops'] },
  { label: '📈 Business Analyst', keywords: ['business analyst', 'business analysis', 'operations', 'strategy'] },
  { label: '📣 Marketing', keywords: ['marketing', 'growth', 'brand', 'content'] },
  { label: '🔐 Cybersecurity', keywords: ['security', 'cyber', 'infosec', 'security engineer'] },
  { label: '💡 Consulting', keywords: ['consulting', 'consultant', 'advisory'] },
  { label: '🎨 Creatives and Design', keywords: ['design', 'designer', 'creative', 'ux', 'ui'] },
  { label: '🧑‍💼 Management and Executive', keywords: ['management', 'executive', 'leadership', 'director', 'vp'] },
  { label: '🏛️ Public Sector and Government', keywords: ['government', 'public sector', 'public policy', 'civil service'] },
  { label: '⚖️ Legal and Compliance', keywords: ['legal', 'compliance', 'attorney', 'law', 'regulatory'] },
  { label: '👥 Human Resources', keywords: ['human resources', 'hr', 'recruiting', 'talent'] },
  { label: '🎭 Arts and Entertainment', keywords: ['arts', 'entertainment', 'creative', 'media', 'content'] },
  { label: '💹 Sales', keywords: ['sales', 'account executive', 'business development', 'bdr', 'sdr'] },
  { label: '🎧 Customer Service and Support', keywords: ['customer service', 'support', 'success', 'client support', 'help desk'] },
  { label: '🎓 Education and Training', keywords: ['education', 'training', 'instruction', 'learning', 'teacher'] },
  { label: '🏥 Healthcare', keywords: ['healthcare', 'hospital', 'clinical', 'medical', 'patient'] },
  { label: '🚚 Supply Chain', keywords: ['supply chain', 'logistics', 'operations', 'procurement', 'inventory'] },
] as const;

const CATEGORY_OPTIONS = CATEGORY_DEFINITIONS.map((category) => category.label);

export default function InternJobsAlertsPage({ onBack }: InternJobsAlertsPageProps) {
  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<(typeof CATEGORY_OPTIONS)[number] | null>(null);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const filteredJobs = useMemo(() => {
    const jobs = dashboard?.jobs ?? [];
    if (!selectedCategory) {
      return jobs;
    }
    const keywords = CATEGORY_DEFINITIONS.find((category) => category.label === selectedCategory)?.keywords ?? [];
    return jobs.filter((job) => {
      const title = `${job.title || ''}`.toLowerCase();
      const company = `${job.company || ''}`.toLowerCase();
      return keywords.some((keyword) => title.includes(keyword) || company.includes(keyword));
    });
  }, [dashboard?.jobs, selectedCategory]);

  const loadData = async (isMounted?: () => boolean) => {
    setLoading(true);
    setError('');
    try {
      const dashboardPayload = await fetchInternJobsDashboard();

      if (isMounted && !isMounted()) {
        return;
      }

      setDashboard(dashboardPayload);
    } catch (loadError) {
      if (isMounted && !isMounted()) {
        return;
      }

      setError(loadError instanceof Error ? loadError.message : 'Failed to load top jobs.');
    } finally {
      if (!isMounted || isMounted()) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    let mounted = true;
    loadData(() => mounted);

    return () => {
      mounted = false;
    };
  }, []);

  const handleFetchNow = async () => {
    setTriggering(true);
    setError('');
    setNotice('');
    try {
      const result = await triggerInternJobsPipeline();
      await loadData();
      if (result.newJobsCount === 0) {
        setNotice(`No new jobs detected. Showing current Top ${result.topCount} jobs.`);
      } else {
        setNotice(`Fetch completed. Showing Top ${result.topCount} jobs.`);
      }
    } catch (triggerError) {
      setError(triggerError instanceof Error ? triggerError.message : 'Failed to trigger pipeline run.');
    } finally {
      setTriggering(false);
    }
  };

  return (
    <section className="flex h-full w-full flex-col bg-bg-main text-text-primary transition-colors duration-300">
      <div className="border-b border-border-color px-4 py-4 md:px-6">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-4">
          <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold">Top 100 Intern Jobs</h2>
            <p className="text-sm text-text-secondary">Latest pipeline snapshot only.</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleFetchNow}
              disabled={triggering}
              className="rounded-md bg-sjsu-gold px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-sjsu-gold-hover disabled:opacity-60"
            >
              {triggering ? 'Fetching...' : 'Fetch Now'}
            </button>
            <button
              type="button"
              onClick={onBack}
              className="rounded-md border border-border-color bg-bg-surface px-3 py-2 text-sm text-text-primary transition-colors hover:bg-bg-hover"
            >
              Back
            </button>
          </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-text-secondary">
              <span>21 Categories</span>
              <button
                type="button"
                onClick={() => setSelectedCategory(null)}
                className="normal-case text-xs text-text-secondary underline decoration-border-color underline-offset-2 hover:text-text-primary"
              >
                Show all
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-4">
              {CATEGORY_OPTIONS.map((category) => (
                <button
                  key={category}
                  type="button"
                  onClick={() => setSelectedCategory((current) => (current === category ? null : category))}
                  className={`rounded-full border px-3 py-2 text-left text-sm font-medium transition-colors md:px-4 ${
                    selectedCategory === category
                      ? 'border-transparent bg-sjsu-gold text-white shadow-sm'
                      : 'border-border-color bg-bg-surface text-text-primary hover:bg-bg-hover'
                  }`}
                >
                  {category}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4 md:p-6">
        <div className="mx-auto w-full max-w-6xl space-y-4">
          {loading && <div className="text-sm text-text-secondary">Loading top jobs...</div>}
          {!loading && !error && notice && <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-300">{notice}</div>}
          {!loading && error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300">{error}</div>}
          {!loading && !error && (
            <>
              <div className="text-sm text-text-secondary">
                Showing {filteredJobs.length} of {(dashboard?.jobs ?? []).length} jobs
                {selectedCategory ? ` for ${selectedCategory}` : ''}.
              </div>
              <JobsDashboardTable jobs={filteredJobs} />
            </>
          )}
        </div>
      </div>
    </section>
  );
}
