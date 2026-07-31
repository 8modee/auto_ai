# agents/__init__.py
"""
THE INSTITUTION — Agent Package
Makes the agents directory a proper Python package.
"""

from agents.base import BaseAgent
from agents.content_site import ContentSiteAgent
from agents.product_engine import ProductEngineAgent
from agents.video_engine import VideoEngineAgent
from agents.newsletter import NewsletterAgent
from agents.grant_pipeline import GrantPipelineAgent
from agents.freelance_pipeline import FreelancePipelineAgent
from agents.print_on_demand import PrintOnDemandAgent
from agents.affiliate_sites import AffiliateSitesAgent
from agents.social_media import SocialMediaAgent
from agents.micro_saas import MicroSaasAgent
from agents.niche_scout import NicheScoutAgent
from agents.oracle import OracleAgent
from agents.safety_officer import SafetyOfficerAgent
from agents.security_buffer import SecurityBufferAgent

__all__ = [
    "BaseAgent",
    "ContentSiteAgent",
    "ProductEngineAgent",
    "VideoEngineAgent",
    "NewsletterAgent",
    "GrantPipelineAgent",
    "FreelancePipelineAgent",
    "PrintOnDemandAgent",
    "AffiliateSitesAgent",
    "SocialMediaAgent",
    "MicroSaasAgent",
    "NicheScoutAgent",
    "OracleAgent",
    "SafetyOfficerAgent",
    "SecurityBufferAgent",
]