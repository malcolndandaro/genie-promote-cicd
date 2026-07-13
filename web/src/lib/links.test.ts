import { describe, it, expect } from 'vitest';
import { genieSpaceUrl } from './links';

describe('genieSpaceUrl', () => {
  it('builds the room URL from a bare host', () => {
    expect(genieSpaceUrl('dev.cloud.databricks.com', 'sp-1')).toBe(
      'https://dev.cloud.databricks.com/genie/rooms/sp-1',
    );
  });

  it('normalizes a full https URL host (as APP_DEV_HOST is often configured)', () => {
    expect(genieSpaceUrl('https://dev.cloud.databricks.com', 'sp-1')).toBe(
      'https://dev.cloud.databricks.com/genie/rooms/sp-1',
    );
  });

  it('strips a trailing slash', () => {
    expect(genieSpaceUrl('https://dev.cloud.databricks.com/', 'sp-1')).toBe(
      'https://dev.cloud.databricks.com/genie/rooms/sp-1',
    );
  });
});
