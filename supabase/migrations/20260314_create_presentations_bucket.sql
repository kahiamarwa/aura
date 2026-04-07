-- ============================================================
-- Bucket Storage pour les présentations PPTX générées par AURA
-- ============================================================

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'presentations',
  'presentations',
  false,
  10485760,  -- 10MB
  ARRAY['application/vnd.openxmlformats-officedocument.presentationml.presentation']
)
ON CONFLICT (id) DO NOTHING;

-- RLS: users can only read their own presentations (stored under user_id/ prefix)
CREATE POLICY "Users can read own presentations"
ON storage.objects FOR SELECT
USING (bucket_id = 'presentations' AND auth.uid()::text = (storage.foldername(name))[1]);

-- RLS: users can delete their own presentations
CREATE POLICY "Users can delete own presentations"
ON storage.objects FOR DELETE
USING (bucket_id = 'presentations' AND auth.uid()::text = (storage.foldername(name))[1]);
