import { useEffect, useState } from 'react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { StatCard } from '@/components/dashboard/StatCard';
import { useAuth } from '@/contexts/AuthContext';
import { authApi, type AdminStats } from '@/lib/auth';
import { FileText, Users, Play, AlertCircle, CheckCircle2, Clock, TrendingUp, Loader2 } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';

const CHART_COLORS = [
  'hsl(187, 100%, 50%)', // primary cyan
  'hsl(145, 80%, 45%)', // success
  'hsl(36, 95%, 55%)',  // warning
  'hsl(0, 72%, 55%)',   // destructive
  'hsl(215, 80%, 55%)', // listed blue
];

export default function AdminDashboard() {
  const { token } = useAuth();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [tendersByDate, setTendersByDate] = useState<{ date: string; count: number }[]>([]);
  const [categoriesData, setCategoriesData] = useState<{ category: string; count: number }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;
    const fetchAll = async () => {
      setLoading(true);
      const [statsRes, dateRes, catRes] = await Promise.all([
        authApi.getAdminStats(token),
        authApi.getTendersByDate(token),
        authApi.getTendersByCategory(token),
      ]);
      if (statsRes.success && statsRes.data) setStats(statsRes.data);
      if (dateRes.success && dateRes.data) setTendersByDate(dateRes.data.reverse());
      if (catRes.success && catRes.data) setCategoriesData(catRes.data.slice(0, 8));
      setLoading(false);
    };
    fetchAll();
  }, [token]);

  if (loading) {
    return (
      <AdminLayout>
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-8 h-8 animate-spin text-primary" />
        </div>
      </AdminLayout>
    );
  }

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="text-muted-foreground text-sm mt-1">Vue d'ensemble de la plateforme</p>
        </div>

        {/* Stat Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            label="Total Appels d'Offres"
            value={stats?.tenders.total ?? 0}
            icon={<FileText className="w-4 h-4" />}
            variant="primary"
          />
          <StatCard
            label="Analysés"
            value={stats?.tenders.analyzed ?? 0}
            icon={<CheckCircle2 className="w-4 h-4" />}
            variant="success"
          />
          <StatCard
            label="Clients Actifs"
            value={stats?.clients.active ?? 0}
            icon={<Users className="w-4 h-4" />}
          />
          <StatCard
            label="Taux de Succès Scraper"
            value={`${stats?.scraper.success_rate ?? 0}%`}
            icon={<TrendingUp className="w-4 h-4" />}
            variant="success"
          />
        </div>

        {/* Secondary Stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard label="En attente" value={stats?.tenders.pending ?? 0} variant="warning" />
          <StatCard label="Erreurs" value={stats?.tenders.error ?? 0} variant="destructive" />
          <StatCard label="Clients en attente" value={stats?.clients.pending_approval ?? 0} variant="warning" />
          <StatCard label="Jobs Scraper" value={stats?.scraper.total_jobs ?? 0} icon={<Play className="w-4 h-4" />} />
        </div>

        {/* Charts */}
        <div className="grid md:grid-cols-2 gap-6">
          {/* Tenders by Date */}
          <div className="data-card">
            <h3 className="font-medium mb-4">Appels d'Offres par Date</h3>
            {tendersByDate.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <BarChart data={tendersByDate}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(215, 25%, 18%)" />
                  <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(215, 15%, 55%)' }} />
                  <YAxis tick={{ fontSize: 10, fill: 'hsl(215, 15%, 55%)' }} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'hsl(222, 47%, 10%)',
                      border: '1px solid hsl(215, 25%, 18%)',
                      borderRadius: '8px',
                      fontSize: '12px',
                    }}
                  />
                  <Bar dataKey="count" fill="hsl(187, 100%, 50%)" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="text-center py-10 text-muted-foreground text-sm">Aucune donnée</div>
            )}
          </div>

          {/* Categories Pie */}
          <div className="data-card">
            <h3 className="font-medium mb-4">Répartition par Catégorie</h3>
            {categoriesData.length > 0 ? (
              <ResponsiveContainer width="100%" height={250}>
                <PieChart>
                  <Pie
                    data={categoriesData}
                    cx="50%"
                    cy="50%"
                    labelLine={false}
                    label={({ category, percent }) => `${category.slice(0, 15)} (${(percent * 100).toFixed(0)}%)`}
                    outerRadius={80}
                    dataKey="count"
                  >
                    {categoriesData.map((_, i) => (
                      <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <div className="text-center py-10 text-muted-foreground text-sm">Aucune donnée</div>
            )}
          </div>
        </div>

        {/* Recent Jobs */}
        <div className="data-card">
          <h3 className="font-medium mb-4">Historique des Jobs Scraper</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left py-2 px-3 text-muted-foreground font-medium">Date Cible</th>
                  <th className="text-left py-2 px-3 text-muted-foreground font-medium">Statut</th>
                  <th className="text-right py-2 px-3 text-muted-foreground font-medium">Trouvés</th>
                  <th className="text-right py-2 px-3 text-muted-foreground font-medium">Téléchargés</th>
                  <th className="text-right py-2 px-3 text-muted-foreground font-medium">Échoués</th>
                  <th className="text-right py-2 px-3 text-muted-foreground font-medium">Durée</th>
                  <th className="text-left py-2 px-3 text-muted-foreground font-medium">Démarré</th>
                </tr>
              </thead>
              <tbody>
                {(stats?.recent_jobs ?? []).map((job) => (
                  <tr key={job.id} className="border-b border-border/50">
                    <td className="py-2 px-3 font-mono">{job.target_date}</td>
                    <td className="py-2 px-3">
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                        job.status === 'COMPLETED' ? 'bg-success/15 text-success' :
                        job.status === 'FAILED' ? 'bg-destructive/15 text-destructive' :
                        job.status === 'RUNNING' ? 'bg-primary/15 text-primary' :
                        'bg-muted text-muted-foreground'
                      }`}>
                        {job.status}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-right font-mono">{job.total_found}</td>
                    <td className="py-2 px-3 text-right font-mono text-success">{job.downloaded}</td>
                    <td className="py-2 px-3 text-right font-mono text-destructive">{job.failed}</td>
                    <td className="py-2 px-3 text-right font-mono">{job.elapsed_seconds ? `${job.elapsed_seconds}s` : '—'}</td>
                    <td className="py-2 px-3 text-xs text-muted-foreground">
                      {job.started_at ? new Date(job.started_at).toLocaleString('fr-FR') : '—'}
                    </td>
                  </tr>
                ))}
                {(!stats?.recent_jobs || stats.recent_jobs.length === 0) && (
                  <tr>
                    <td colSpan={7} className="py-8 text-center text-muted-foreground">Aucun job récent</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </AdminLayout>
  );
}
