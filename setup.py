from setuptools import setup, find_namespace_packages

setup(
    name='ckanext-malmo',
    version='0.1',
    description="Customizations for Malmo Stad",
    long_description=""" """,
    classifiers=[],
    keywords='',
    author='Datopian',
    author_email='info@datopian.com',
    url='',
    license='AGPL',
    packages=find_namespace_packages(include=['ckanext.*']),
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        # list of dependencies
    ],
    entry_points='''
        [ckan.plugins]
        malmo=ckanext.malmo.plugin:MalmoPlugin
    ''',
)
