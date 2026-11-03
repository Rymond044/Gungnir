// Stroke icons of the navigation, drawn inline so they take the text colour.

const props = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.6,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
}

export const OverviewIcon = () => (
  <svg {...props}>
    <rect x="3.5" y="3.5" width="7" height="7" rx="1" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="1" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="1" />
  </svg>
)

export const AnomalyIcon = () => (
  <svg {...props}>
    <path d="M3 15h4l2.5-8 4 12 2.5-6H21" />
  </svg>
)

export const AnalysisIcon = () => (
  <svg {...props}>
    <circle cx="10.5" cy="10.5" r="6" />
    <path d="M15 15l5.5 5.5" />
  </svg>
)

export const KnowledgeIcon = () => (
  <svg {...props}>
    <path d="M5 4.5h10.5a3 3 0 0 1 3 3v12H8a3 3 0 0 1-3-3z" />
    <path d="M5 16.5a3 3 0 0 1 3-3h10.5" />
  </svg>
)
