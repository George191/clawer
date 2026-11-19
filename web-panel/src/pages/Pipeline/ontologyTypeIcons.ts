import {
  AppstoreOutlined, DatabaseOutlined, FileTextOutlined, GlobalOutlined,
  PushpinOutlined, RadarChartOutlined, RocketOutlined, SafetyCertificateOutlined,
  ScheduleOutlined, TableOutlined, TeamOutlined, ToolOutlined, UserOutlined,
} from '@/components/ui/icons';

export const ontologyTypeIcons = {
  box: AppstoreOutlined, database: DatabaseOutlined, table: TableOutlined,
  globe: GlobalOutlined, 'map-pin': PushpinOutlined, radar: RadarChartOutlined,
  rocket: RocketOutlined, users: TeamOutlined, person: UserOutlined,
  document: FileTextOutlined, calendar: ScheduleOutlined, tool: ToolOutlined,
  security: SafetyCertificateOutlined,
} as const;

export function ontologyTypeIcon(name: string) {
  return Object.prototype.hasOwnProperty.call(ontologyTypeIcons, name)
    ? ontologyTypeIcons[name as keyof typeof ontologyTypeIcons] : undefined;
}
