import { useId } from 'react'
import { cn } from '@/lib/utils'
import { useTheme, type ColorScheme } from '@/contexts/ThemeContext'

export type HeroAnimationType = 'spin' | false

interface LogoProps {
  className?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
  animated?: boolean
  heroAnimation?: HeroAnimationType
  variant?: 'gradient' | 'mono' | 'themed'
}

const sizeClasses = {
  sm: 'w-6 h-6',
  md: 'w-8 h-8',
  lg: 'w-10 h-10',
  xl: 'w-14 h-14',
}

// Gradient color configurations for each color scheme
interface SchemeGradientConfig {
  dark: string
  primary: string
  mid: string
  secondary: string
  bright: string
  play1: string
  play2: string
}

const schemeGradients: Record<ColorScheme, SchemeGradientConfig> = {
  mediafusion: {
    dark: 'rgb(61,6,94)',
    primary: 'rgb(103,42,122)',
    mid: 'rgb(135,60,104)',
    secondary: 'rgb(214,102,60)',
    bright: 'rgb(245,149,51)',
    play1: 'rgb(236,107,51)',
    play2: 'rgb(245,149,51)',
  },
  cinematic: {
    dark: 'rgb(92,64,12)',
    primary: 'rgb(139,98,23)',
    mid: 'rgb(180,133,45)',
    secondary: 'rgb(212,168,83)',
    bright: 'rgb(245,215,142)',
    play1: 'rgb(212,168,83)',
    play2: 'rgb(245,215,142)',
  },
  ocean: {
    dark: 'rgb(7,59,76)',
    primary: 'rgb(2,132,199)',
    mid: 'rgb(14,165,233)',
    secondary: 'rgb(56,189,248)',
    bright: 'rgb(125,211,252)',
    play1: 'rgb(14,165,233)',
    play2: 'rgb(125,211,252)',
  },
  forest: {
    dark: 'rgb(6,78,59)',
    primary: 'rgb(5,150,105)',
    mid: 'rgb(16,185,129)',
    secondary: 'rgb(52,211,153)',
    bright: 'rgb(110,231,183)',
    play1: 'rgb(16,185,129)',
    play2: 'rgb(110,231,183)',
  },
  rose: {
    dark: 'rgb(136,19,55)',
    primary: 'rgb(225,29,72)',
    mid: 'rgb(244,63,94)',
    secondary: 'rgb(251,113,133)',
    bright: 'rgb(253,164,175)',
    play1: 'rgb(244,63,94)',
    play2: 'rgb(253,164,175)',
  },
  purple: {
    dark: 'rgb(46,16,101)',
    primary: 'rgb(109,40,217)',
    mid: 'rgb(139,92,246)',
    secondary: 'rgb(167,139,250)',
    bright: 'rgb(196,181,253)',
    play1: 'rgb(139,92,246)',
    play2: 'rgb(196,181,253)',
  },
  sunset: {
    dark: 'rgb(124,45,18)',
    primary: 'rgb(194,65,12)',
    mid: 'rgb(234,88,12)',
    secondary: 'rgb(249,115,22)',
    bright: 'rgb(253,186,116)',
    play1: 'rgb(249,115,22)',
    play2: 'rgb(253,186,116)',
  },
  youtube: {
    dark: 'rgb(127,0,0)',
    primary: 'rgb(185,0,0)',
    mid: 'rgb(220,38,38)',
    secondary: 'rgb(255,0,0)',
    bright: 'rgb(255,78,69)',
    play1: 'rgb(255,0,0)',
    play2: 'rgb(255,78,69)',
  },
}

// Get animation classes for each logo part based on heroAnimation type
function getAnimationClasses(heroAnimation: HeroAnimationType) {
  if (!heroAnimation) {
    return { arrows: '', play: '', wrapper: '' }
  }

  // Circular spin animation - both arrows rotate together as one unit, play button pulses
  return {
    wrapper: 'animate-hero-entrance',
    arrows: 'animate-spin-arrows',
    play: 'animate-pulse-play',
  }
}

/**
 * MediaFusion Logo Component
 * Properly vectorized from Affinity Designer export
 * Supports three variants:
 * - gradient: Original brand colors (purple/orange)
 * - mono: Single color using currentColor for theme adaptability
 * - themed: Gradient colors that match the current color scheme
 *
 * Hero animations (for large hero logos):
 * - converge: Arrows slide in from opposite directions
 * - orbital: Subtle rotation around center
 * - draw: SVG paths draw themselves in sequence
 * - ripple: Play button pulses with ripple effect
 * - breathe: Arrows expand/contract like breathing
 */
export function Logo({
  className,
  size = 'md',
  animated = true,
  heroAnimation = false,
  variant = 'themed',
}: LogoProps) {
  const id = useId()
  const { colorScheme } = useTheme()

  const colors = variant === 'themed' ? schemeGradients[colorScheme] : schemeGradients.mediafusion
  const animClasses = getAnimationClasses(heroAnimation)

  const gradientIds = {
    linear1: `${id}-linear1`,
    linear2: `${id}-linear2`,
    linear3: `${id}-linear3`,
    linear4: `${id}-linear4`,
    linear5: `${id}-linear5`,
    linear6: `${id}-linear6`,
    linear7: `${id}-linear7`,
    linear8: `${id}-linear8`,
    linear9: `${id}-linear9`,
    linear10: `${id}-linear10`,
  }

  if (variant === 'mono') {
    return (
      <svg
        viewBox="0 0 2500 2500"
        className={cn(
          sizeClasses[size],
          animated && !heroAnimation && 'transition-transform duration-300 group-hover:scale-105',
          animClasses.wrapper,
          className,
        )}
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <g transform="translate(-295.5,-834.397847)">
          {/* Both Arrows wrapped together for synchronized rotation */}
          <g className={animClasses.arrows} style={{ transformOrigin: '1545px 2085px' }}>
            {/* Upper Arrow */}
            <g>
              <path
                d="M1045,1906L899,1818C1399.404,1172.404 2536.032,1126.877 2735,2091C2676.009,964.626 1584.778,653.234 667,1657L522,1561L491,2167L1045,1906Z"
                fill="currentColor"
                opacity="0.9"
              />
              <path
                d="M524,1486C824.546,974.804 1472.686,766.455 2044,1011C1533.464,916.851 1016.558,1144.339 658,1578L524,1486Z"
                fill="currentColor"
                opacity="0.7"
              />
              <path
                d="M1367,1453C2038.269,1111.154 2702.533,1344.171 2735,2091C2574.168,1372.002 1947.277,1211.848 1367,1453Z"
                fill="currentColor"
                opacity="0.75"
              />
              <path
                d="M490,2166C979.374,1230.007 2092.096,716.423 2641,1642C2289.628,766.894 1282.268,952.517 667,1657L521,1560L490,2166Z"
                fill="currentColor"
                opacity="0.85"
              />
            </g>
            {/* Down Arrow (mirrored) */}
            <g transform="matrix(-1,0,0,-1,3091,4168.795694)">
              <path
                d="M1045,1906L899,1818C1399.404,1172.404 2536.032,1126.877 2735,2091C2676.009,964.626 1584.778,653.234 667,1657L522,1561L491,2167L1045,1906Z"
                fill="currentColor"
                opacity="0.9"
              />
              <path
                d="M524,1486C824.546,974.804 1472.686,766.455 2044,1011C1533.464,916.851 1016.558,1144.339 658,1578L524,1486Z"
                fill="currentColor"
                opacity="0.7"
              />
              <path
                d="M1367,1453C2038.269,1111.154 2702.533,1344.171 2735,2091C2574.168,1372.002 1947.277,1211.848 1367,1453Z"
                fill="currentColor"
                opacity="0.75"
              />
              <path
                d="M490,2166C979.374,1230.007 2092.096,716.423 2641,1642C2289.628,766.894 1282.268,952.517 667,1657L521,1560L490,2166Z"
                fill="currentColor"
                opacity="0.85"
              />
            </g>
          </g>
          {/* Play Icon */}
          <g className={animClasses.play} style={{ transformOrigin: '1545px 2085px' }}>
            <g transform="matrix(0,1.108723,-0.986778,0,3706.314245,281.596713)">
              <path
                d="M1580.87,1759.065C1588.809,1744.98 1602.635,1736.469 1617.473,1736.532C1632.311,1736.594 1646.079,1745.223 1653.923,1759.374C1712.531,1866.453 1818.697,2066.174 1982.758,2400.036C1990.16,2415.051 1989.944,2433.344 1982.189,2448.132C1974.434,2462.92 1960.297,2471.997 1945.021,2471.997C1793.726,2472 1447.553,2472 1297.987,2472C1282.912,2472 1268.93,2463.159 1261.107,2448.68C1253.284,2434.2 1252.755,2416.184 1259.711,2401.157C1358.92,2186.761 1458.215,1977.573 1580.87,1759.065Z"
                fill="currentColor"
                opacity="0.95"
              />
            </g>
            <g transform="matrix(0,1,-0.890013,0,3494.111252,460)">
              <path
                d="M1579.129,1776.229C1587.613,1759.464 1603.442,1749.092 1620.606,1749.05C1637.77,1749.008 1653.639,1759.302 1662.188,1776.025C1738.711,1925.706 1900.707,2242.572 1976.837,2391.485C1985.34,2408.116 1985.31,2428.579 1976.758,2445.179C1968.207,2461.779 1952.43,2472 1935.359,2472C1783.318,2472 1460.958,2472 1309.227,2472C1292.194,2472 1276.445,2461.823 1267.88,2445.28C1259.315,2428.737 1259.226,2408.325 1267.646,2391.688C1342.933,2242.928 1503.299,1926.061 1579.129,1776.229Z"
                fill="currentColor"
              />
            </g>
          </g>
        </g>
      </svg>
    )
  }

  // Gradient variant (original brand) or Themed variant (color scheme adaptive)
  return (
    <svg
      viewBox="0 0 2500 2500"
      className={cn(
        sizeClasses[size],
        animated && !heroAnimation && 'transition-transform duration-300 group-hover:scale-105',
        animClasses.wrapper,
        className,
      )}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ fillRule: 'evenodd', clipRule: 'evenodd', strokeLinejoin: 'round', strokeMiterlimit: 2 }}
    >
      <defs>
        {/* Upper Arrow Gradients */}
        <linearGradient
          id={gradientIds.linear1}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(1204,-1225,1225,1204,474,2213)"
        >
          <stop offset="0" stopColor={colors.dark} />
          <stop offset="0.14" stopColor={colors.primary} />
          <stop offset="0.5" stopColor={colors.primary} />
          <stop offset="0.7" stopColor={colors.mid} />
          <stop offset="0.95" stopColor={colors.secondary} />
          <stop offset="1" stopColor={colors.bright} />
        </linearGradient>
        <linearGradient
          id={gradientIds.linear2}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(974,-782,782,974,629,1426)"
        >
          <stop offset="0" stopColor={colors.dark} />
          <stop offset="0.37" stopColor={colors.dark} />
          <stop offset="0.58" stopColor={colors.primary} />
          <stop offset="0.8" stopColor={colors.mid} />
          <stop offset="1" stopColor={colors.secondary} />
        </linearGradient>
        <linearGradient
          id={gradientIds.linear3}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(1316,146,-146,1316,1480,1311)"
        >
          <stop offset="0" stopColor={colors.dark} />
          <stop offset="0.64" stopColor={colors.primary} />
          <stop offset="0.84" stopColor={colors.mid} />
          <stop offset="1" stopColor={colors.secondary} />
        </linearGradient>
        <linearGradient
          id={gradientIds.linear4}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(1264,-1234,1234,1264,640,1793)"
        >
          <stop offset="0" stopColor={colors.dark} />
          <stop offset="0.42" stopColor={colors.primary} />
          <stop offset="0.59" stopColor={colors.mid} />
          <stop offset="0.74" stopColor={colors.secondary} />
          <stop offset="1" stopColor={colors.bright} />
        </linearGradient>

        {/* Down Arrow Gradients */}
        <linearGradient
          id={gradientIds.linear5}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(-1101,833,-833,-1101,2221,949.795694)"
        >
          <stop offset="0" stopColor={colors.dark} />
          <stop offset="0.14" stopColor={colors.primary} />
          <stop offset="0.53" stopColor={colors.mid} />
          <stop offset="0.88" stopColor={colors.secondary} />
          <stop offset="1" stopColor={colors.bright} />
        </linearGradient>
        <linearGradient
          id={gradientIds.linear6}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(-956,751,-751,-956,1636,688.795694)"
        >
          <stop offset="0" stopColor={colors.dark} />
          <stop offset="0.37" stopColor={colors.dark} />
          <stop offset="0.58" stopColor={colors.primary} />
          <stop offset="0.8" stopColor={colors.mid} />
          <stop offset="1" stopColor={colors.secondary} />
        </linearGradient>
        <linearGradient
          id={gradientIds.linear7}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(-1058,-82,82,-1058,2711,1601.795694)"
        >
          <stop offset="0" stopColor={colors.dark} />
          <stop offset="0.58" stopColor={colors.mid} />
          <stop offset="1" stopColor={colors.secondary} />
        </linearGradient>
        <linearGradient
          id={gradientIds.linear8}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(-2071,1112,-1112,-2071,2391,852.795694)"
        >
          <stop offset="0" stopColor={colors.dark} />
          <stop offset="0.74" stopColor={colors.secondary} />
          <stop offset="1" stopColor={colors.bright} />
        </linearGradient>

        {/* Play Icon Gradients */}
        <linearGradient
          id={gradientIds.linear9}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(-457.282782,-513.793581,457.282782,-513.793581,1922.394527,2522.669978)"
        >
          <stop offset="0" stopColor={colors.play1} />
          <stop offset="0.57" stopColor={colors.play2} />
          <stop offset="1" stopColor={colors.bright} />
        </linearGradient>
        <linearGradient
          id={gradientIds.linear10}
          x1="0"
          y1="0"
          x2="1"
          y2="0"
          gradientUnits="userSpaceOnUse"
          gradientTransform="matrix(-479,-475.274148,423,-538.194602,1926,2533.796875)"
        >
          <stop offset="0" stopColor={colors.secondary} />
          <stop offset="0.43" stopColor={colors.play1} />
          <stop offset="1" stopColor={colors.bright} />
        </linearGradient>
      </defs>

      <g transform="translate(-295.5,-834.397847)">
        {/* Both Arrows wrapped together for synchronized rotation */}
        <g className={animClasses.arrows} style={{ transformOrigin: '1545px 2085px' }}>
          {/* Upper Arrow */}
          <g>
            <path
              d="M1045,1906L899,1818C1399.404,1172.404 2536.032,1126.877 2735,2091C2676.009,964.626 1584.778,653.234 667,1657L522,1561L491,2167L1045,1906Z"
              fill={`url(#${gradientIds.linear1})`}
            />
            <path
              d="M524,1486C824.546,974.804 1472.686,766.455 2044,1011C1533.464,916.851 1016.558,1144.339 658,1578L524,1486Z"
              fill={`url(#${gradientIds.linear2})`}
            />
            <path
              d="M1367,1453C2038.269,1111.154 2702.533,1344.171 2735,2091C2574.168,1372.002 1947.277,1211.848 1367,1453Z"
              fill={`url(#${gradientIds.linear3})`}
            />
            <path
              d="M490,2166C979.374,1230.007 2092.096,716.423 2641,1642C2289.628,766.894 1282.268,952.517 667,1657L521,1560L490,2166Z"
              fill={`url(#${gradientIds.linear4})`}
            />
          </g>

          {/* Down Arrow (mirrored) */}
          <g transform="matrix(-1,0,0,-1,3091,4168.795694)">
            <path
              d="M1045,1906L899,1818C1399.404,1172.404 2536.032,1126.877 2735,2091C2676.009,964.626 1584.778,653.234 667,1657L522,1561L491,2167L1045,1906Z"
              fill={`url(#${gradientIds.linear5})`}
            />
            <path
              d="M524,1486C824.546,974.804 1472.686,766.455 2044,1011C1533.464,916.851 1016.558,1144.339 658,1578L524,1486Z"
              fill={`url(#${gradientIds.linear6})`}
            />
            <path
              d="M1367,1453C2038.269,1111.154 2702.533,1344.171 2735,2091C2574.168,1372.002 1947.277,1211.848 1367,1453Z"
              fill={`url(#${gradientIds.linear7})`}
            />
            <path
              d="M490,2166C979.374,1230.007 2092.096,716.423 2641,1642C2289.628,766.894 1282.268,952.517 667,1657L521,1560L490,2166Z"
              fill={`url(#${gradientIds.linear8})`}
            />
          </g>
        </g>

        {/* Play Icon */}
        <g className={animClasses.play} style={{ transformOrigin: '1545px 2085px' }}>
          <g transform="matrix(0,1.108723,-0.986778,0,3706.314245,281.596713)">
            <path
              d="M1580.87,1759.065C1588.809,1744.98 1602.635,1736.469 1617.473,1736.532C1632.311,1736.594 1646.079,1745.223 1653.923,1759.374C1712.531,1866.453 1818.697,2066.174 1982.758,2400.036C1990.16,2415.051 1989.944,2433.344 1982.189,2448.132C1974.434,2462.92 1960.297,2471.997 1945.021,2471.997C1793.726,2472 1447.553,2472 1297.987,2472C1282.912,2472 1268.93,2463.159 1261.107,2448.68C1253.284,2434.2 1252.755,2416.184 1259.711,2401.157C1358.92,2186.761 1458.215,1977.573 1580.87,1759.065Z"
              fill={`url(#${gradientIds.linear9})`}
            />
          </g>
          <g transform="matrix(0,1,-0.890013,0,3494.111252,460)">
            <path
              d="M1579.129,1776.229C1587.613,1759.464 1603.442,1749.092 1620.606,1749.05C1637.77,1749.008 1653.639,1759.302 1662.188,1776.025C1738.711,1925.706 1900.707,2242.572 1976.837,2391.485C1985.34,2408.116 1985.31,2428.579 1976.758,2445.179C1968.207,2461.779 1952.43,2472 1935.359,2472C1783.318,2472 1460.958,2472 1309.227,2472C1292.194,2472 1276.445,2461.823 1267.88,2445.28C1259.315,2428.737 1259.226,2408.325 1267.646,2391.688C1342.933,2242.928 1503.299,1926.061 1579.129,1776.229Z"
              fill={`url(#${gradientIds.linear10})`}
            />
          </g>
        </g>
      </g>
    </svg>
  )
}

// Text gradient configurations for each color scheme
// Separate light and dark mode gradients for better visibility
interface TextGradientConfig {
  first: { top: string; bottom: string }
  second: { stops: string[] }
}

interface TextGradientThemeConfig {
  light: TextGradientConfig
  dark: TextGradientConfig
}

const textGradients: Record<ColorScheme, TextGradientThemeConfig> = {
  mediafusion: {
    light: {
      first: { top: 'rgb(193,94,253)', bottom: 'rgb(69,35,114)' },
      second: { stops: ['rgb(242,166,64)', 'rgb(224,101,57)', 'rgb(201,84,73)', 'rgb(143,41,115)', 'rgb(103,42,122)'] },
    },
    dark: {
      first: { top: 'rgb(216,180,254)', bottom: 'rgb(147,87,219)' },
      second: {
        stops: ['rgb(253,224,71)', 'rgb(251,191,36)', 'rgb(245,158,11)', 'rgb(217,119,106)', 'rgb(192,132,252)'],
      },
    },
  },
  cinematic: {
    light: {
      first: { top: 'rgb(245,215,142)', bottom: 'rgb(120,90,30)' },
      second: { stops: ['rgb(245,215,142)', 'rgb(212,168,83)', 'rgb(180,133,45)', 'rgb(139,98,23)', 'rgb(100,75,20)'] },
    },
    dark: {
      first: { top: 'rgb(254,249,195)', bottom: 'rgb(250,204,21)' },
      second: { stops: ['rgb(254,249,195)', 'rgb(253,224,71)', 'rgb(250,204,21)', 'rgb(234,179,8)', 'rgb(202,138,4)'] },
    },
  },
  ocean: {
    light: {
      first: { top: 'rgb(125,211,252)', bottom: 'rgb(14,116,144)' },
      second: { stops: ['rgb(125,211,252)', 'rgb(56,189,248)', 'rgb(14,165,233)', 'rgb(2,132,199)', 'rgb(7,89,133)'] },
    },
    dark: {
      first: { top: 'rgb(224,242,254)', bottom: 'rgb(56,189,248)' },
      second: {
        stops: ['rgb(224,242,254)', 'rgb(186,230,253)', 'rgb(125,211,252)', 'rgb(56,189,248)', 'rgb(14,165,233)'],
      },
    },
  },
  forest: {
    light: {
      first: { top: 'rgb(110,231,183)', bottom: 'rgb(22,101,52)' },
      second: { stops: ['rgb(110,231,183)', 'rgb(52,211,153)', 'rgb(16,185,129)', 'rgb(5,150,105)', 'rgb(4,120,87)'] },
    },
    dark: {
      first: { top: 'rgb(209,250,229)', bottom: 'rgb(52,211,153)' },
      second: {
        stops: ['rgb(209,250,229)', 'rgb(167,243,208)', 'rgb(110,231,183)', 'rgb(52,211,153)', 'rgb(16,185,129)'],
      },
    },
  },
  rose: {
    light: {
      first: { top: 'rgb(253,164,175)', bottom: 'rgb(159,18,57)' },
      second: { stops: ['rgb(253,164,175)', 'rgb(251,113,133)', 'rgb(244,63,94)', 'rgb(225,29,72)', 'rgb(159,18,57)'] },
    },
    dark: {
      first: { top: 'rgb(255,228,230)', bottom: 'rgb(251,113,133)' },
      second: {
        stops: ['rgb(255,228,230)', 'rgb(254,205,211)', 'rgb(253,164,175)', 'rgb(251,113,133)', 'rgb(244,63,94)'],
      },
    },
  },
  purple: {
    light: {
      first: { top: 'rgb(196,181,253)', bottom: 'rgb(91,33,182)' },
      second: {
        stops: ['rgb(196,181,253)', 'rgb(167,139,250)', 'rgb(139,92,246)', 'rgb(109,40,217)', 'rgb(76,29,149)'],
      },
    },
    dark: {
      first: { top: 'rgb(243,232,255)', bottom: 'rgb(167,139,250)' },
      second: {
        stops: ['rgb(243,232,255)', 'rgb(233,213,255)', 'rgb(196,181,253)', 'rgb(167,139,250)', 'rgb(139,92,246)'],
      },
    },
  },
  sunset: {
    light: {
      first: { top: 'rgb(253,186,116)', bottom: 'rgb(154,52,18)' },
      second: { stops: ['rgb(253,186,116)', 'rgb(249,115,22)', 'rgb(234,88,12)', 'rgb(194,65,12)', 'rgb(154,52,18)'] },
    },
    dark: {
      first: { top: 'rgb(255,237,213)', bottom: 'rgb(251,146,60)' },
      second: {
        stops: ['rgb(255,237,213)', 'rgb(254,215,170)', 'rgb(253,186,116)', 'rgb(251,146,60)', 'rgb(249,115,22)'],
      },
    },
  },
  youtube: {
    light: {
      first: { top: 'rgb(255,78,69)', bottom: 'rgb(153,27,27)' },
      second: { stops: ['rgb(255,78,69)', 'rgb(255,0,0)', 'rgb(220,38,38)', 'rgb(185,28,28)', 'rgb(153,27,27)'] },
    },
    dark: {
      first: { top: 'rgb(254,202,202)', bottom: 'rgb(248,113,113)' },
      second: { stops: ['rgb(254,202,202)', 'rgb(252,165,165)', 'rgb(248,113,113)', 'rgb(255,78,69)', 'rgb(255,0,0)'] },
    },
  },
}

// Text size classes mapping
const textSizeClasses = {
  sm: 'text-base',
  md: 'text-lg',
  lg: 'text-xl',
  xl: 'text-2xl',
  '2xl': 'text-3xl',
  '3xl': 'text-4xl',
  '4xl': 'text-5xl',
  '5xl': 'text-6xl',
}

type TextSize = keyof typeof textSizeClasses

interface LogoTextProps {
  className?: string
  addonName?: string
  size?: TextSize
  variant?: 'gradient' | 'themed'
  suffixClassName?: string
}

/**
 * Parse addon name to extract the main name and any suffix after separators like |, -, :
 * e.g., "MediaFusion | ElfHosted" -> { mainName: "MediaFusion", suffix: "ElfHosted" }
 * e.g., "MediaFusion" -> { mainName: "MediaFusion", suffix: null }
 */
function parseAddonName(addonName: string): { mainName: string; suffix: string | null } {
  // Check for common separators: |, -, :
  const separatorMatch = addonName.match(/^(.+?)\s*[|:\-–—]\s*(.+)$/)
  if (separatorMatch) {
    return {
      mainName: separatorMatch[1].trim(),
      suffix: separatorMatch[2].trim(),
    }
  }
  return { mainName: addonName, suffix: null }
}

/**
 * Standalone gradient text component for the logo name
 * Can be used independently without the icon
 * Automatically adapts colors for light/dark mode
 * Supports addon names with separators like "MediaFusion | ElfHosted"
 */
export function LogoText({
  className,
  addonName = 'MediaFusion',
  size = 'md',
  variant = 'themed',
  suffixClassName,
}: LogoTextProps) {
  const { colorScheme, resolvedTheme } = useTheme()

  // Parse addon name to handle separators
  const { mainName, suffix } = parseAddonName(addonName)

  // Split main name into parts for gradient (e.g., "MediaFusion" -> "Media" + "Fusion")
  const nameParts = mainName.match(/([A-Z][a-z]+)/g) || [mainName]
  const firstPart = nameParts[0] || mainName
  const restParts = nameParts.length > 1 ? nameParts.slice(1).join('') : ''

  // Use themed gradients or default to mediafusion, with light/dark mode support
  const schemeGradients =
    variant === 'themed' ? textGradients[colorScheme] || textGradients.mediafusion : textGradients.mediafusion

  // Select the appropriate gradient based on current theme mode
  const gradientColors = resolvedTheme === 'dark' ? schemeGradients.dark : schemeGradients.light

  return (
    <span
      className={cn(
        'font-display font-black tracking-tight inline-flex items-baseline gap-2',
        textSizeClasses[size],
        className,
      )}
    >
      <span className="inline-flex">
        <span
          style={{
            backgroundImage: `linear-gradient(to bottom, ${gradientColors.first.top}, ${gradientColors.first.bottom})`,
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            color: 'transparent',
          }}
        >
          {firstPart}
        </span>
        <span
          style={{
            backgroundImage: `linear-gradient(to bottom, ${gradientColors.second.stops.join(', ')})`,
            backgroundClip: 'text',
            WebkitBackgroundClip: 'text',
            color: 'transparent',
          }}
        >
          {restParts}
        </span>
      </span>
      {suffix && (
        <>
          <span className={cn('text-muted-foreground font-normal text-[0.6em]', suffixClassName)}>|</span>
          <span className={cn('text-muted-foreground font-semibold text-[0.7em]', suffixClassName)}>{suffix}</span>
        </>
      )}
    </span>
  )
}

interface BrandingLogoProps {
  svgUrl: string
  className?: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
}

const brandingSizeClasses = {
  sm: 'h-6',
  md: 'h-8',
  lg: 'h-10',
  xl: 'h-14',
}

/**
 * Component to display a partner/host branding logo from an SVG URL
 * Shows the logo as-is without color modifications
 */
export function BrandingLogo({ svgUrl, className, size = 'md' }: BrandingLogoProps) {
  return (
    <img
      src={svgUrl}
      alt="Partner Logo"
      className={cn(brandingSizeClasses[size], 'w-auto object-contain', className)}
    />
  )
}

interface LogoWithTextProps extends LogoProps {
  addonName?: string
  textSize?: TextSize
  brandingSvg?: string | null
  suffixClassName?: string
  brandingClassName?: string
}

/**
 * Logo icon with gradient text beside it
 * Optionally displays a partner branding logo
 */
export function LogoWithText({
  className,
  size = 'md',
  animated = true,
  heroAnimation = false,
  variant = 'themed',
  addonName = 'MediaFusion',
  textSize = 'md',
  brandingSvg,
  suffixClassName,
  brandingClassName,
}: LogoWithTextProps) {
  // Map mono variant to themed for text (mono doesn't make sense for gradient text)
  const textVariant = variant === 'mono' ? 'themed' : variant

  return (
    <div className={cn('flex items-center gap-2.5 group', className)}>
      <Logo size={size} animated={animated} heroAnimation={heroAnimation} variant={variant} />
      <LogoText addonName={addonName} size={textSize} variant={textVariant} suffixClassName={suffixClassName} />
      {brandingSvg && (
        <>
          <span className={cn('text-muted-foreground/50 text-lg', brandingClassName)}>×</span>
          <BrandingLogo svgUrl={brandingSvg} size={size} className={brandingClassName} />
        </>
      )}
    </div>
  )
}
