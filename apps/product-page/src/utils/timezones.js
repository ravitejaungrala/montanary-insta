export const timezones = [
    { value: "UTC", label: "(UTC+00:00) Universal Coordinated Time" },
    { value: "Atlantic/Azores", label: "(UTC-01:00) Azores" },
    { value: "America/Noronha", label: "(UTC-02:00) Mid-Atlantic" },
    { value: "America/Sao_Paulo", label: "(UTC-03:00) Brasilia, Sao Paulo" },
    { value: "America/St_Johns", label: "(UTC-03:30) Newfoundland" },
    { value: "America/Argentina/Buenos_Aires", label: "(UTC-03:00) Buenos Aires" },
    { value: "America/Caracas", label: "(UTC-04:00) Caracas" },
    { value: "America/Halifax", label: "(UTC-04:00) Halifax" },
    { value: "America/New_York", label: "(UTC-05:00) Eastern Time (US & Canada)" },
    { value: "America/Chicago", label: "(UTC-06:00) Central Time (US & Canada)" },
    { value: "America/Denver", label: "(UTC-07:00) Mountain Time (US & Canada)" },
    { value: "America/Los_Angeles", label: "(UTC-08:00) Pacific Time (US & Canada)" },
    { value: "America/Anchorage", label: "(UTC-09:00) Alaska" },
    { value: "Pacific/Honolulu", label: "(UTC-10:00) Hawaii" },
    { value: "Pacific/Samoa", label: "(UTC-11:00) Samoa" },
    { value: "Europe/London", label: "(UTC+00:00) London, Dublin, Edinburgh" },
    { value: "Europe/Paris", label: "(UTC+01:00) Brussels, Madrid, Paris" },
    { value: "Europe/Berlin", label: "(UTC+01:00) Berlin, Rome, Vienna" },
    { value: "Europe/Athens", label: "(UTC+02:00) Athens, Istanbul, Minsk" },
    { value: "Europe/Moscow", label: "(UTC+03:00) Moscow, St. Petersburg" },
    { value: "Africa/Cairo", label: "(UTC+02:00) Cairo" },
    { value: "Africa/Johannesburg", label: "(UTC+02:00) Johannesburg" },
    { value: "Asia/Dubai", label: "(UTC+04:00) Abu Dhabi, Muscat" },
    { value: "Asia/Kabul", label: "(UTC+04:30) Kabul" },
    { value: "Asia/Karachi", label: "(UTC+05:00) Islamabad, Karachi" },
    { value: "Asia/Kolkata", label: "(UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi" },
    { value: "Asia/Kathmandu", label: "(UTC+05:45) Kathmandu" },
    { value: "Asia/Dhaka", label: "(UTC+06:00) Astana, Dhaka" },
    { value: "Asia/Rangoon", label: "(UTC+06:30) Rangoon" },
    { value: "Asia/Bangkok", label: "(UTC+07:00) Bangkok, Hanoi, Jakarta" },
    { value: "Asia/Shanghai", label: "(UTC+08:00) Beijing, Chongqing, Hong Kong" },
    { value: "Asia/Singapore", label: "(UTC+08:00) Singapore" },
    { value: "Asia/Tokyo", label: "(UTC+09:00) Osaka, Sapporo, Tokyo" },
    { value: "Asia/Seoul", label: "(UTC+09:00) Seoul" },
    { value: "Australia/Darwin", label: "(UTC+09:30) Darwin" },
    { value: "Australia/Adelaide", label: "(UTC+09:30) Adelaide" },
    { value: "Australia/Sydney", label: "(UTC+10:00) Canberra, Melbourne, Sydney" },
    { value: "Australia/Brisbane", label: "(UTC+10:00) Brisbane" },
    { value: "Pacific/Guadalcanal", label: "(UTC+11:00) Magadan, Solomon Is." },
    { value: "Pacific/Auckland", label: "(UTC+12:00) Auckland, Wellington" },
    { value: "Pacific/Fiji", label: "(UTC+12:00) Fiji, Marshall Is." }
];

export const getDefaultTimezone = () => {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch (e) {
        return "UTC";
    }
};

/**
 * Gets a friendly label for a timezone value.
 * If the value is in our hardcoded list, use that.
 * Otherwise, generate a label like '(UTC+05:30) Asia/Calcutta'
 */
export const getFriendlyTimezoneLabel = (tzValue) => {
    if (!tzValue) return "";
    
    // Check if it exists in the list
    const found = timezones.find(t => t.value === tzValue);
    if (found) return found.label;

    // Fallback: Generate dynamic label
    try {
        const d = new Date();
        const parts = new Intl.DateTimeFormat('en-US', {
            hour12: false,
            timeZone: tzValue,
            timeZoneName: 'shortOffset'
        }).formatToParts(d);
        
        const gmtOffset = parts.find(p => p.type === 'timeZoneName')?.value || "UTC";
        // Format GMT+XX to (UTC+XX:XX)
        const utcLabel = gmtOffset.replace("GMT", "UTC");
        
        return `(${utcLabel}) ${tzValue.split('/').pop().replace('_', ' ')}`;
    } catch (e) {
        return tzValue;
    }
};

/**
 * Returns formatted local time for a specific timezone
 */
export const getCurrentTimeInTimezone = (tzValue) => {
    try {
        return new Intl.DateTimeFormat('en-US', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: true,
            timeZone: tzValue || "UTC"
        }).format(new Date());
    } catch (e) {
        return "";
    }
};

/**
 * Formats a Date or ISO string based on a specific timezone.
 */
export const formatInTimezone = (date, timezone = "UTC", options = {}) => {
    if (!date) return "";
    try {
        const d = typeof date === 'string' ? new Date(date) : date;
        const defaultOptions = {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            timeZone: timezone || "UTC"
        };
        return new Intl.DateTimeFormat('en-US', { ...defaultOptions, ...options }).format(d);
    } catch (e) {
        return new Date(date).toLocaleString(); 
    }
};
