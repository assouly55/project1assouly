import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import type { ScraperStatus } from '@/types/tender';

export function useScraperStatus() {
  return useQuery({
    queryKey: ['scraper-status'],
    queryFn: async () => {
      const result = await api.getScraperStatus();
      if (!result.success) {
        throw new Error(result.error);
      }
      return result.data;
    },
    refetchInterval: 2000, // Poll every 2s when running
  });
}

export function useTriggerScraper() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: async ({ startDate, endDate }: { startDate?: string; endDate?: string }) => {
      const result = await api.triggerScraper(startDate, endDate);
      if (!result.success) {
        throw new Error(result.error);
      }
      return result.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scraper-status'] });
    },
  });
}

export function useStopScraper() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: async () => {
      const result = await api.stopScraper();
      if (!result.success) {
        throw new Error(result.error);
      }
      return result.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scraper-status'] });
      queryClient.invalidateQueries({ queryKey: ['tenders'] });
    },
  });
}
