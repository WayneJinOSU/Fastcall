import { naturalDayCount, shiftDateKey } from './calendar';

describe('calendar fixed-timezone boundaries', () => {
  it('uses fixed Asia/Shanghai arithmetic for shortcuts', () => {
    expect(shiftDateKey('2026-11-01', -1)).toBe('2026-10-31');
    expect(shiftDateKey('2026-03-01', -1)).toBe('2026-02-28');
  });

  it('uses the actual exclusive end time for the 92-day limit', () => {
    expect(naturalDayCount('2026-08-15', '2026-11-15', '00:00', '00:00')).toBe(92);
    expect(naturalDayCount('2026-08-16', '2026-11-15', '00:00', '00:00')).toBe(91);
  });
});
