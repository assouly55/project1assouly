import { useEffect, useState } from 'react';
import { AdminLayout } from '@/components/layout/AdminLayout';
import { useAuth } from '@/contexts/AuthContext';
import { authApi } from '@/lib/auth';
import { Loader2 } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell, LineChart, Line } from 'recharts';

const CHART_COLORS = [
  'hsl(187, 100%, 50%)', 'hsl(145, 80%, 45%)', 'hsl(36, 95%, 55%)',
  'hsl(0, 72%, 55%)', 'hsl(215, 80%, 55%)', 'hsl(280, 70%, 55%)',
  'hsl(160, 70%, 45%)', 'hsl(320, 70%, 55%)',
];

export default function AdminStats() {
  const { token } = useAuth();
  const [tendersByDate, setTendersByDate] = useState<{ date: string; count: number }[]>([]);
  const [categoriesData, setCategoriesData] = useState<{ category: string; count: number }[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token) return;
    const fetchAll = async () => {
      setLoading(true);
      const [dateRes, catRes] = await Promise.all([
        authApi.getTendersByDate(token),
        authApi.getTendersByCategory(token),
      ]);
      if (dateRes.success && dateRes.data) setTendersByDate(dateRes.data.reverse());
      if (catRes.success && catRes.data) setCategoriesData(catRes.data);
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

  // Cumulative chart data
  let cumulative = 0;
  const cumulativeData = tendersByDate.map(d => {
    cumulative += d.count;
    return { date: d.date, total: cumulative };
  });

  return (
    <AdminLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-semibold">Statistiques</h1>
          <p className="text-muted-foreground text-sm mt-1">Analyse détaillée des données</p>
        </div>

        <div className="grid md:grid-cols-2 gap-6">
          {/* Daily tenders */}
          <div className="data-card">
            <h3 className="font-medium mb-4">Appels d'Offres par Jour</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={tendersByDate}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(215, 25%, 18%)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(215, 15%, 55%)' }} />
                <YAxis tick={{ fontSize: 10, fill: 'hsl(215, 15%, 55%)' }} />
                <Tooltip contentStyle={{ backgroundColor: 'hsl(222, 47%, 10%)', border: '1px solid hsl(215, 25%, 18%)', borderRadius: '8px', fontSize: '12px' }} />
                <Bar dataKey="count" fill="hsl(187, 100%, 50%)" radius={[4, 4, 0, 0]} name="Appels d'Offres" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Cumulative */}
          <div className="data-card">
            <h3 className="font-medium mb-4">Évolution Cumulée</h3>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={cumulativeData}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(215, 25%, 18%)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(215, 15%, 55%)' }} />
                <YAxis tick={{ fontSize: 10, fill: 'hsl(215, 15%, 55%)' }} />
                <Tooltip contentStyle={{ backgroundColor: 'hsl(222, 47%, 10%)', border: '1px solid hsl(215, 25%, 18%)', borderRadius: '8px', fontSize: '12px' }} />
                <Line type="monotone" dataKey="total" stroke="hsl(145, 80%, 45%)" strokeWidth={2} dot={false} name="Total Cumulé" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Categories */}
        <div className="grid md:grid-cols-2 gap-6">
          <div className="data-card">
            <h3 className="font-medium mb-4">Répartition par Catégorie</h3>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie data={categoriesData} cx="50%" cy="50%" outerRadius={100} dataKey="count"
                  label={({ category, percent }) => `${category.slice(0, 20)} (${(percent * 100).toFixed(0)}%)`}
                >
                  {categoriesData.map((_, i) => (<Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </div>

          <div className="data-card">
            <h3 className="font-medium mb-4">Catégories (Barres)</h3>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={categoriesData} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(215, 25%, 18%)" />
                <XAxis type="number" tick={{ fontSize: 10, fill: 'hsl(215, 15%, 55%)' }} />
                <YAxis dataKey="category" type="category" width={120} tick={{ fontSize: 10, fill: 'hsl(215, 15%, 55%)' }} />
                <Tooltip contentStyle={{ backgroundColor: 'hsl(222, 47%, 10%)', border: '1px solid hsl(215, 25%, 18%)', borderRadius: '8px', fontSize: '12px' }} />
                <Bar dataKey="count" fill="hsl(36, 95%, 55%)" radius={[0, 4, 4, 0]} name="Nombre" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>
    </AdminLayout>
  );
}
