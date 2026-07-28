import { describe, it, expect } from 'vitest';
import { dashboardUrl, genieSpaceUrl, resourceUrl } from './links';

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

describe('dashboardUrl', () => {
  it('builds the PUBLISHED view — the draft is the authoring surface, not what a consumer opens', () => {
    expect(dashboardUrl('prod.cloud.databricks.com', 'd-1')).toBe(
      'https://prod.cloud.databricks.com/dashboardsv3/d-1/published',
    );
  });

  it('normalizes a full URL host the same way genieSpaceUrl does', () => {
    expect(dashboardUrl('https://prod.cloud.databricks.com/', 'd-1')).toBe(
      'https://prod.cloud.databricks.com/dashboardsv3/d-1/published',
    );
  });
});

describe('resourceUrl', () => {
  it('dispatches on kind so callers never branch', () => {
    expect(resourceUrl('h.example.com', 'genie_space', 'sp-1')).toBe(
      'https://h.example.com/genie/rooms/sp-1',
    );
    expect(resourceUrl('h.example.com', 'dashboard', 'd-1')).toBe(
      'https://h.example.com/dashboardsv3/d-1/published',
    );
  });

  it('returns null on a missing host or id so the caller hides the link instead of rendering a broken one', () => {
    expect(resourceUrl(null, 'dashboard', 'd-1')).toBeNull();
    expect(resourceUrl('h.example.com', 'dashboard', null)).toBeNull();
    expect(resourceUrl(undefined, 'genie_space', 'sp-1')).toBeNull();
  });
});
