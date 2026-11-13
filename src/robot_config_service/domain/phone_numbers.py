import phonenumbers

from robot_config_service.domain.errors import RobotConfigError


def normalize_e164(value, default_country_code):
    if not isinstance(value, str) or not value.strip():
        raise RobotConfigError("INVALID_PHONE_NUMBER", "Phone number is required")
    value = value.strip()
    try:
        if value.startswith("+"):
            parsed = phonenumbers.parse(value, None)
        else:
            regions = phonenumbers.region_codes_for_country_code(int(default_country_code))
            if len(regions) != 1:
                raise RobotConfigError(
                    "INVALID_PHONE_NUMBER",
                    "A national number requires an unambiguous default country code; use E.164",
                )
            parsed = phonenumbers.parse(value, regions[0])
    except (phonenumbers.NumberParseException, ValueError) as error:
        raise RobotConfigError("INVALID_PHONE_NUMBER", "Phone number is not valid") from error
    if not phonenumbers.is_valid_number(parsed):
        raise RobotConfigError("INVALID_PHONE_NUMBER", "Phone number is not valid")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
