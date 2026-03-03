import logging
import ckan.plugins as plugins
from ckan.plugins import toolkit

from ckanext.malmo import actions as malmo_actions

log = logging.getLogger(__name__)


class MalmoPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IPackageController, inherit=True)
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IActions)

    def update_config(self, config):
        """
        We have some form snippets that support ckanext-scheming
        """
        toolkit.add_template_directory(config, 'templates')
        toolkit.add_resource('assets', 'malmo')

    def get_actions(self):
        return {
            'package_update': malmo_actions.package_update,
            'package_create': malmo_actions.package_create,
            'package_patch': malmo_actions.package_patch,
            'package_show': malmo_actions.package_show,
            'package_search': malmo_actions.package_search,
            'resource_create': malmo_actions.resource_create,
            'resource_update': malmo_actions.resource_update,
            'resource_patch': malmo_actions.resource_patch,
            'organization_create': malmo_actions.organization_create,
            'organization_update': malmo_actions.organization_update,
            'organization_patch': malmo_actions.organization_patch,
            'organization_show': malmo_actions.organization_show,
            'group_create': malmo_actions.group_create,
            'group_update': malmo_actions.group_update,
            'group_patch': malmo_actions.group_patch,
            'group_show': malmo_actions.group_show,
            'group_list': malmo_actions.group_list,
        }
