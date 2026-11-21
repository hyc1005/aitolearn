import type { ThemeConfig } from 'antd'

const palette = {
  paper: '#F6F3EC',
  surface: '#FFFDFA',
  answer: '#F3EEE3',
  ink: '#202536',
  muted: '#565D70',
  line: '#DCD9D0',
  nav: '#202542',
  navHighlight: '#343C69',
  navText: '#DCE0F2',
  navRule: '#515877',
  accent: '#384581',
  accentHover: '#2D396D',
  tint: '#E6E9F5',
  heatLow: '#EEF0F8',
  heatHigh: '#A9B3DA',
  neutral: '#EEEBE4',
  compare: '#6D6082',
  annotation: '#A63D31',
  annotationTint: '#F9E3D8',
  success: '#3A6857',
  warning: '#8C591E',
} as const

export function applyWorkbenchPalette() {
  for (const [key, value] of Object.entries(palette)) {
    document.documentElement.style.setProperty('--' + key.replace(/[A-Z]/g, char => '-' + char.toLowerCase()), value)
  }
}

export const workbenchTheme: ThemeConfig = {
  token: {
    colorPrimary: palette.accent,
    colorPrimaryHover: palette.accentHover,
    colorLink: palette.accent,
    colorText: palette.ink,
    colorTextSecondary: palette.muted,
    colorTextPlaceholder: palette.muted,
    colorBgLayout: palette.paper,
    colorBgContainer: palette.surface,
    colorBgElevated: palette.surface,
    colorBorder: palette.line,
    colorSuccess: palette.success,
    colorWarning: palette.warning,
    colorError: palette.annotation,
    controlHeight: 36,
    borderRadius: 6,
    fontFamily: '"Microsoft YaHei UI", "Microsoft YaHei", sans-serif',
    fontSize: 20,
    fontSizeSM: 18,
    motion: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  },
  components: {
    Button: { primaryShadow: 'none' },
    Layout: { siderBg: palette.nav },
    Menu: {
      darkItemBg: palette.nav,
      darkItemColor: palette.navText,
      darkItemSelectedBg: palette.navHighlight,
      darkItemSelectedColor: palette.surface,
      darkItemHoverBg: palette.navHighlight,
      darkItemHoverColor: palette.surface,
      itemBorderRadius: 4,
    },
    Segmented: { trackBg: palette.neutral, itemSelectedBg: palette.surface },
    Table: { headerBg: palette.paper, headerColor: palette.ink },
  },
}

export const chartComparisonColor = palette.compare
