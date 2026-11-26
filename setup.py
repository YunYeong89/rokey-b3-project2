from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'vision_project'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'resource'), glob('resource/*.npy')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='ms-07',
    maintainer_email='ms-07@todo.todo',
    description='TODO: Package description',
    license='Apache-2.0',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': [
            'detection = vision_project.detection:main',
            'pick = vision_project.pick:main',
        ],
    },
)
