-- Allow PDF files in the presentations bucket (for PDF export of PPTX)
UPDATE storage.buckets
SET allowed_mime_types = ARRAY[
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/pdf'
]
WHERE id = 'presentations';
