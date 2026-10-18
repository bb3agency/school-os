// SchoolOS UI primitives. docs/17-ui-design-system.md lists them with do/don't; the dev-only
// page /[locale]/dev/ui shows every variant.
export { AiPanel, QuoteBlock, type AiSuggestion } from "./AiPanel";
export { Alert, type AlertProps, type AlertTone } from "./Alert";
export { Avatar, AvatarStack, initials, type AvatarSize } from "./Avatar";
export {
  Badge,
  DeltaPill,
  Pill,
  type BadgeTone,
  type DeltaDirection,
  type PillVariant,
} from "./Badge";
export {
  Button,
  ButtonLink,
  IconButton,
  buttonClasses,
  iconButtonClasses,
  type ButtonProps,
  type ButtonSize,
  type ButtonVariant,
  type IconButtonProps,
  type IconButtonSize,
  type IconButtonVariant,
} from "./Button";
export {
  Card,
  CardHeader,
  cardClasses,
  type CardHeaderProps,
  type CardPadding,
  type CardProps,
  type CardTone,
} from "./Card";
export {
  DataTable,
  StackedRows,
  stackPlace,
  Table,
  TableScroll,
  TBody,
  THead,
  Td,
  Th,
  Tr,
  type Column,
  type StackedRowsProps,
  type TableDensity,
  type TableProps,
  type TableScrollProps,
} from "./Table";
export { NarrowSwitch } from "./NarrowSwitch";
export { Dialog } from "./Dialog";
export { EmptyState } from "./EmptyState";
export {
  Illustration,
  ILLUSTRATIONS,
  illustrationSrc,
  type IllustrationName,
} from "./Illustration";
export { Eyebrow, type EyebrowTone } from "./Eyebrow";
export { Icon, ICON_NAMES, type IconName } from "./Icon";
export {
  Field,
  Input,
  SearchInput,
  TextAreaField,
  TextField,
  Textarea,
  controlClasses,
  type SearchInputProps,
} from "./Input";
export { Label } from "./Label";
export { LanguageSwitcher } from "./LanguageSwitcher";
export { LoadingState, Skeleton } from "./LoadingState";
export { Breadcrumb, PageHeader, type Crumb } from "./PageHeader";
export { ProgressRing, type ProgressRingSize } from "./ProgressRing";
export {
  SegmentedControl,
  type SegmentOption,
  type SegmentedControlProps,
} from "./SegmentedControl";
export { Select, SelectField, type SelectOption } from "./Select";
export { SidebarNav, type NavItem, type NavSection } from "./SidebarNav";
export { Sparkline, type ChartSeries } from "./Sparkline";
export { KpiCard, StatCard, type KpiCardProps, type KpiDelta } from "./StatCard";
export { TabNav } from "./TabNav";
export { Tabs, type TabItem, type TabsVariant } from "./Tabs";
export { Timeline, type TimelineItem, type TimelineStatus } from "./Timeline";
export { Toggle, type ToggleProps } from "./Toggle";
export { UsageMeter } from "./UsageMeter";
export { Drawer, type DrawerProps } from "./Drawer";
export { ErrorSummary, type SummaryError } from "./ErrorSummary";
export { FilterChips, type FilterChip } from "./FilterChips";
export { Kbd } from "./Kbd";
export { Menu, type MenuEntry, type MenuProps } from "./Menu";
export { Pagination, type PaginationProps } from "./Pagination";
export { Popover, type PopoverProps } from "./Popover";
export { SaveBar } from "./SaveBar";
export { Steps, type Step } from "./Steps";
export { ToastProvider, useToast, type ToastInput, type ToastTone } from "./Toaster";
