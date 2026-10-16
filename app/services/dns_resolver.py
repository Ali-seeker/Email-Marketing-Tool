"""DNS resolution interface and implementations for MX and A record lookups.

Follows RULES.md: all external network requests are hidden behind an interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class DnsResult:
    """DNS lookup outcome for a domain."""

    domain: str
    has_mx: bool
    has_a: bool
    is_valid: bool
    records: list[str] = field(default_factory=list)


class DnsResolver(ABC):
    """Abstract interface for domain DNS resolution."""

    @abstractmethod
    def resolve(self, domain: str) -> DnsResult:
        """Resolve MX and A records for a domain.

        Args:
            domain: Lowercase target domain name (e.g. 'example.com').

        Returns:
            DnsResult containing has_mx, has_a, is_valid flag, and raw records.
        """
        pass


class LiveDnsResolver(DnsResolver):
    """Production DNS resolver utilizing dnspython for real DNS queries."""

    def __init__(self, timeout: float = 3.0) -> None:
        """Initialize live resolver with query timeout."""
        self.timeout = timeout

    def resolve(self, domain: str) -> DnsResult:
        """Resolve MX and fallback A records using dnspython."""
        import dns.exception
        import dns.resolver

        resolver = dns.resolver.Resolver()
        resolver.lifetime = self.timeout

        has_mx = False
        has_null_mx = False
        has_a = False
        records: list[str] = []

        # 1. Check MX records
        try:
            mx_answers = resolver.resolve(domain, "MX")
            for rdata in mx_answers:
                raw_exchange = rdata.exchange.to_text().strip()
                cleaned_exchange = raw_exchange.rstrip(".")
                if not cleaned_exchange or raw_exchange == ".":
                    # RFC 7505 Null MX ("MX 0 .") explicitly declares domain accepts NO mail
                    has_null_mx = True
                    records.append(
                        f"Null MX: . (priority {rdata.preference}) - Domain accepts no email"
                    )
                else:
                    has_mx = True
                    records.append(f"MX: {raw_exchange} (priority {rdata.preference})")
        except (
            dns.resolver.NoAnswer,
            dns.resolver.NXDOMAIN,
            dns.exception.Timeout,
            Exception,
        ) as err:
            logger.debug("MX lookup failed for %s: %s", domain, err)

        # 2. Check A records fallback (RFC 5321 specifies A fallback ONLY when NO MX exists at all,
        # NEVER on Null MX)
        if not has_null_mx:
            try:
                a_answers = resolver.resolve(domain, "A")
                for rdata in a_answers:
                    has_a = True
                    records.append(f"A: {rdata.address}")
            except (
                dns.resolver.NoAnswer,
                dns.resolver.NXDOMAIN,
                dns.exception.Timeout,
                Exception,
            ) as err:
                logger.debug("A lookup failed for %s: %s", domain, err)

        is_valid = has_mx or (has_a and not has_null_mx)
        return DnsResult(
            domain=domain,
            has_mx=has_mx,
            has_a=has_a,
            is_valid=is_valid,
            records=records,
        )


class FakeDnsResolver(DnsResolver):
    """In-memory mock DNS resolver for deterministic tests."""

    def __init__(self, default_valid: bool = True) -> None:
        """Initialize fake resolver with default status and explicit registry."""
        self._registry: dict[str, DnsResult] = {}
        self.default_valid = default_valid

    def set_domain(
        self,
        domain: str,
        has_mx: bool,
        has_a: bool,
        records: list[str] | None = None,
    ) -> None:
        """Configure explicit DNS outcome for a domain."""
        norm_domain = domain.strip().lower()
        self._registry[norm_domain] = DnsResult(
            domain=norm_domain,
            has_mx=has_mx,
            has_a=has_a,
            is_valid=has_mx or has_a,
            records=records or [],
        )

    def resolve(self, domain: str) -> DnsResult:
        """Return configured DNS response or default."""
        norm_domain = domain.strip().lower()
        if norm_domain in self._registry:
            return self._registry[norm_domain]

        return DnsResult(
            domain=norm_domain,
            has_mx=self.default_valid,
            has_a=self.default_valid,
            is_valid=self.default_valid,
            records=["MOCK_MX: mail.mock.com"] if self.default_valid else [],
        )
